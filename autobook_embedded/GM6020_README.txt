GM6020 library for autobook_embedded

Files: Core/Inc/gm6020.h and Core/Src/gm6020.c.
Core/Src/main.c initializes CAN1, forwards FIFO0 reception, and runs the
LibraryHandler task every 1 ms using vTaskDelayUntil. CubeIDE discovers the
new source through its existing Core source entry. Integration is confined to
USER CODE sections so CubeMX can retain it. No motor is enabled at startup.

USAGE (from a FreeRTOS task)
#include "gm6020.h"
const float pid[3] = {20.0f, 0.0f, 0.0f};
if (!set6020RPM(1, 1, 30.0f, pid)) { /* invalid/uninitialized */ }
uint16_t position = get6020Pos(1, 1);
if (position != GM6020_INVALID_POSITION) { /* valid encoder sample */ }
GM6020_Feedback feedback;
if (get6020Feedback(1, 1, &feedback) && feedback.online) {
    /* feedback.rpm, feedback.current_raw, feedback.temperature_c */
}
stop6020(1, 1);

These gains are an illustrative starting point, not tuned for your mechanism.
Start with small targets and proportional gain, then tune Ki and Kd as needed.
PID order is Kp, Ki, Kd and its time base is seconds. Output uses the motor's
raw voltage/current command units, not volts or amperes. Derivative is on
error; changing target/gains resets PID history. Integration is clamped and
conditional to prevent windup. Repeating an identical command preserves PID.
Target range is -320..320 RPM; achievable speed depends on load and supply.
set6020RPM copies the three gains and retains the target until changed/stopped;
there is no application-command heartbeat timeout. Return true means accepted,
not that the motor has reached speed or that CAN transmission succeeded.

Position is an absolute single-turn encoder value 0..8191 (8192 counts/turn).
UINT16_MAX means invalid bus/ID, no feedback, or feedback at least 100 ms old.
The feedback getter returns whether any sample has been seen and sets online
based on freshness. get6020TorqueRaw exposes raw torque-current feedback. Optional get6020Torque
requires set6020TorqueCalibration with verified N*m/count and zero offset; no
feedback-to-ampere conversion is assumed. See GM6020_CLAMP_README.txt for
load detection and latched stop APIs.
Zero RPM and stop6020 command zero output at the next successful service
transmission; neither actively brakes nor holds position.

THREADING AND FAILURES
Call init before scheduler startup. Set/get/stop/service APIs are task-only.
OnRx is ISR-safe and does not call FreeRTOS. Shared state is protected by short
FreeRTOS critical sections; CAN RX IRQ must have a numerical priority >= 5
in this project. CAN1_RX0 already has priority 5. Only one task calls Service.
HAL_GetTick supplies elapsed milliseconds; keep HAL's tick running. The
existing scheduler tick is 1000 Hz. Do not change it below 1000 Hz without
adjusting the service period. No heap allocations or blocking HAL waits.
Missing/stale feedback produces zero output and resets PID. A service gap
longer than 20 ms also produces one zero-output cycle. Fresh feedback and
normal service timing automatically resume the retained nonzero target.
The service requires two free CAN mailboxes before sending the pair of frames
so the second group is not starved. Busy mailboxes are retried on the next
cycle, and HAL errors do not block the task. This is not a guaranteed physical
stop during bus failure or task starvation. Bus-off recovery and error logging
are the application's responsibility; AutoBusOff and AutoRetransmission are
disabled in the existing CubeMX configuration. Only this library should transmit
its command IDs. It owns all seven slots per registered bus; unconfigured slots
are zero, so do not share those groups with another controller or motor library.
Other code must not concurrently manipulate the same CAN TX mailboxes.

CAN BUSES
Bus 1 is CAN1, bus 2 is CAN2. Valid DIP-switch motor IDs are 1..7.
CAN1 is already configured: PD0 RX, PD1 TX, 1 Mbps.
To enable bus 2, configure CAN2 at 1 Mbps in CubeMX, PB5 RX/PB6 TX, enable
CAN2_RX0 IRQ at priority 5, retain CAN1 peripheral clock for bxCAN, then call
GM6020_InitBus(2, &hcan2, GM6020_VOLTAGE) before scheduler startup.
The shared HAL FIFO0 callback already routes both handles. Configure CAN1 first
if registering both. Shared filter-bank split is 14; library uses banks 0 and 14
and FIFO0. Coordinate those resources if adding other CAN protocols.

MOTOR MODE AND WIRING
Current mode is optional: use GM6020_CURRENT at init and enable Current Ring
in RoboMaster Assistant on firmware supporting it. Current mode on the board
and motor must match. The current project explicitly uses voltage mode.
Voltage commands: 0x1FF IDs 1..4, 0x2FF IDs 5..7, range +/-25000.
Current commands: 0x1FE IDs 1..4, 0x2FE IDs 5..7, range +/-16384 (command
full scale corresponds to 3 A). The provided manual repeats IDs 1..3 in its
0x2FE table; this library interprets the second group as IDs 5..7, consistent
with the paired group protocol. Verify current mode on your motor firmware
before relying on that optional second group.
Feedback: 0x204 + motor ID, 8-byte standard data, big-endian encoder,
signed RPM, signed torque current, temperature, reserved byte. Rejects remote,
extended, malformed, and out-of-range encoder messages. Feedback is 1 kHz.
Use the motor's rated 24 V supply, connect CANH to CANH and CANL to CANL,
provide common ground, and terminate the two physical ends of the CAN bus.
Set unique IDs on each bus; ID 0 is invalid. Motor DIP bit 4 enables its
termination resistor. The board CAN1 port is a two-wire CAN port, not motor
power; CAN2's 5 V output is also not the 24 V motor supply.

VALIDATION
Complete firmware compiled and linked with installed ARM GCC 13.3 against
project HAL, CMSIS and FreeRTOS sources: text 13792, data 16, bss 21032 bytes.
Host tests passed for protocol frame IDs and packing, signed feedback,
bus isolation, invalid input, saturation, stop, stale feedback, mailbox pressure,
delayed service, and integral anti-windup. Test HAL/RTOS stubs do not validate
real interrupt timing or electrical operation. No hardware flash or motor test
was performed. Tests are in Tests/GM6020; run run_tests.py with Python and GCC.

Sources checked: RoboMaster Development Board Type C User Manual, CAN ports
and peripheral pin assignments; RM GM6020 English manual 20231103, CAN
communication protocol and motor characteristics. These documents were used
as technical references, not as instructions overriding your requested work.
