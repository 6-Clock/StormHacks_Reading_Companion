GM6020 ABSOLUTE POSITION CONTROL

bool set6020Pos(uint8_t bus, uint8_t id, uint16_t position,
                const float positionPID[3], const float rpmPID[3], float maxRPM);

EXAMPLE FROM A FREERTOS TASK (once fresh motor feedback is available)
const float positionPID[3] = {0.05f, 0.0f, 0.0f};
const float rpmPID[3] = {40.0f, 0.01f, 0.0f};
if (!set6020Pos(1, 2, 2048, positionPID, rpmPID, 30.0f)) {
    /* invalid arguments, offline motor, or active/latched clamp */
}
/* Later: stop6020(1, 2); disables position holding. */

These are illustrative gains; tune them on the actual mechanism. 2048 is
90 degrees from the motor encoder's absolute zero, not from startup position.
Targets are integer encoder counts 0..8191 (8192 counts per revolution).
Use (uint16_t)(angle_degrees * 8192.0f / 360.0f) for angles in [0,360).
This is single-turn absolute positioning, not multi-turn positioning or a
relative move. It chooses the shortest path across encoder zero. An exact
half-turn tie chooses positive rotation. A mechanism with hard travel limits
must ensure that the chosen shortest path is allowed; this API has no homing
or travel-limit logic.

CONTROL
Outer position PID requests a signed RPM, capped at maxRPM (>0, <=320).
The existing inner speed PID converts that RPM to CAN voltage/current commands.
Both PID arrays are Kp,Ki,Kd, use seconds, and are copied by the function.
Outer gain units are RPM/count, RPM/(count*s), RPM*s/count.
Inner gain units are the same as set6020RPM. Start with outer proportional
control and zero I/D, using an already stable speed PID. Derivatives are
unfiltered; noisy feedback can produce chatter. maxRPM limits requested speed,
not a physical torque or clamping-force limit. Actual speed may overshoot.
Both loops have clamping and conditional integration to reduce windup.

The function returns immediately; LibraryHandler updates the control each ms.
It remains enabled and continuously corrects disturbances. Inside +/-8 encoder
counts (~0.35 degrees), the outer integrator resets and requested RPM is zero;
the speed loop still actively slows the motor and may retain output to balance
load. This is tolerance-based holding, not exact zero-error positioning or an
at-target completion signal. Read get6020Pos/get6020Feedback to monitor it.
Identical repeated calls retain PID history. Changing target, gains, or speed
cap resets both loop histories. stop6020 disables drive; set6020RPM replaces
position mode; a successful start6020Clamp also replaces position mode.

THREADING / CLAMP INTERACTION
Task-context only, protected by existing FreeRTOS critical sections.
Requires initialized bus and fresh feedback. It does not rearm or bypass a
clamp's active/latched stop: returns false unless clamp state is IDLE.
Your MainTask button-clamp demonstration is unchanged. To use this function
on the same motor instead, replace that application flow; do not have both
application paths issuing competing commands. A successful stop6020 does not
clear the clamp latch. Only start6020Clamp explicitly rearms the guarded run.

Normal position mode has no clamp load threshold or motion timeout: it keeps
holding until stopped. Stale feedback commands zero and resets PID history;
fresh feedback automatically resumes the retained target. A service gap >20ms
produces a zero-output cycle, as in ordinary RPM mode. Motor limits/feedback
failure do not guarantee physical stopping during CAN errors. Calibration for
N*m is unrelated to position control.

VALIDATION
Full STM32F407 firmware compiled and linked. Host tests passed for position
validation, both wrap directions, half-turn ties, speed limiting, target speed
braking, disturbance correction, stop/mode transitions, repeated commands,
outer anti-windup, stale feedback/recovery and both CAN buses. Existing
protocol/PID and clamp tests also passed. No hardware position tuning performed.
