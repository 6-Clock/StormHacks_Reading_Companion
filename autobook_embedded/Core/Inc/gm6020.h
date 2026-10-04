#ifndef GM6020_H
#define GM6020_H
#include "stm32f4xx_hal.h"
#include <stdbool.h>
#ifdef __cplusplus
extern "C" {
#endif
#define GM6020_INVALID_POSITION UINT16_MAX
#define GM6020_FEEDBACK_TIMEOUT_MS 100U
typedef enum { GM6020_VOLTAGE, GM6020_CURRENT } GM6020_Mode;
typedef struct {
    uint16_t position; /* 0..8191, one mechanical revolution */
    int16_t rpm, current_raw;
    uint8_t temperature_c;
    uint32_t timestamp_ms;
    bool online;
} GM6020_Feedback;
/* Startup only, before scheduler. Reserves filter bank 0 or 14 and FIFO0.
 * CAN handle must already be initialized at 1 Mbps. Mode must match Assistant.
 * Bus numbers are 1 and 2; motor IDs are 1..7. */
HAL_StatusTypeDef GM6020_InitBus(uint8_t bus, CAN_HandleTypeDef *can, GM6020_Mode mode);
/* Task context only. PID[3] = {Kp, Ki, Kd}; time base is seconds.
 * Gains have units command/rpm, command/(rpm*s), command*s/rpm.
 * Copies gains; sets a persistent target. Returns false for invalid arguments.
 * Zero RPM disables drive and resets PID (does not actively hold position). */
bool set6020RPM(uint8_t bus, uint8_t id, float rpm, const float pid[3]);
/* Single-turn absolute position 0..8191. Takes the shortest path across zero;
 * exactly half a turn chooses positive rotation. Task context only.
 * Outer position PID units: RPM/count, RPM/(count*s), RPM*s/count.
 * Inner rpmPID uses set6020RPM gain units. maxRPM is >0 and <=320.
 * Copies gains, returns immediately, and continuously holds the target.
 * Requires fresh feedback; rejects active/latched clamp states.
 * Within 8 encoder counts, outer loop requests zero RPM (active speed braking).
 * stop6020 disables holding. Ordinary RPM commands replace position mode. */
bool set6020Pos(uint8_t bus, uint8_t id, uint16_t position,
                const float positionPID[3], const float rpmPID[3], float maxRPM);
bool stop6020(uint8_t bus, uint8_t id);
/* Task context only. Snapshot returns false until first feedback; online
 * distinguishes stale feedback. Position returns UINT16_MAX when offline. */
bool get6020Feedback(uint8_t bus, uint8_t id, GM6020_Feedback *out);
uint16_t get6020Pos(uint8_t bus, uint8_t id);
/* Raw signed torque-current feedback, NOT N*m. False when offline. */
bool get6020TorqueRaw(uint8_t bus, uint8_t id, int16_t *current_raw);
/* Supply a verified/calibrated N*m per feedback count and zero-current offset.
 * No factory feedback scaling is assumed. Positive scale, offset in raw counts. */
bool set6020TorqueCalibration(uint8_t bus, uint8_t id, float nm_per_raw, int16_t zero_raw);
/* Motor electromagnetic torque estimate; false when offline/uncalibrated.
 * Does not measure clamp force or account for gearing/friction. */
bool get6020Torque(uint8_t bus, uint8_t id, float *torque_nm);
typedef enum {
    GM6020_CLAMP_IDLE, GM6020_CLAMP_RUNNING, GM6020_CLAMP_CONTACT,
    GM6020_CLAMP_FEEDBACK_LOST, GM6020_CLAMP_TIMEOUT,
    GM6020_CLAMP_SERVICE_LATE, GM6020_CLAMP_STOPPED
} GM6020_ClampState;
typedef struct {
    uint16_t threshold_raw; /* abs(current_raw), 1..32768; determine experimentally */
    uint32_t confirm_ms; /* 0 = first crossing; otherwise sustained crossing */
    uint32_t max_run_ms; /* nonzero maximum closure time, must exceed confirm_ms */
    uint16_t command_limit; /* nonzero, <= bus mode command maximum */
} GM6020_ClampConfig;
typedef struct {
    GM6020_ClampState state;
    uint16_t peak_current_raw;
} GM6020_ClampStatus;
/* Starts one closure, requires fresh feedback and nonzero target. Explicitly
 * rearms any latched stop; call once per closure, NOT on every task iteration.
 * No startup blanking: load detection starts with the next received frame.
 * Stops drive on contact, lost feedback, late service, or timeout. */
bool start6020Clamp(uint8_t bus, uint8_t id, float rpm, const float pid[3],
                    const GM6020_ClampConfig *config);
bool get6020ClampStatus(uint8_t bus, uint8_t id, GM6020_ClampStatus *out);
/* While running, ordinary nonzero RPM changes are rejected. After a guarded
 * stop, ordinary nonzero RPM commands remain rejected until start6020Clamp.
 * stop6020/zero RPM may always stop; they do not clear a latched stop. */
/* Exactly one task must call Service periodically, nominally every 1 ms.
 * Owns transmission of both grouped command frames on each registered bus. */
void GM6020_Service(void);
/* Forward FIFO0 frames from HAL callback; ISR-safe, no RTOS calls. */
void GM6020_OnRx(CAN_HandleTypeDef *can, const CAN_RxHeaderTypeDef *header,
                 const uint8_t data[8]);
#ifdef __cplusplus
}
#endif
#endif
