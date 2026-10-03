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
bool stop6020(uint8_t bus, uint8_t id);
/* Task context only. Snapshot returns false until first feedback; online
 * distinguishes stale feedback. Position returns UINT16_MAX when offline. */
bool get6020Feedback(uint8_t bus, uint8_t id, GM6020_Feedback *out);
uint16_t get6020Pos(uint8_t bus, uint8_t id);
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
