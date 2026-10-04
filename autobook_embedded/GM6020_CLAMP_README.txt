GM6020 BOOK CLAMP: CURRENT FEEDBACK AND CONTACT STOP

Implemented in Core/Inc/gm6020.h and Core/Src/gm6020.c.
MainTask now uses a three-state sequence instead of direction toggling.
See BOOK_STATE_MACHINE_README.txt for the button sequence and servo values.
The motor is stopped at boot in state 1. State 2 runs guarded +60 RPM, while
state 3 and state 1 command zero drive. RunBookClamp remains outside MainTask.
The existing 1 ms LibraryHandler automatically handles the stop.

READ LOAD
int16_t raw;
if (get6020TorqueRaw(1, 2, &raw)) {
    /* signed torque-current feedback counts; false if unavailable or stale */
}
For detecting contact you can use raw current, without knowing its ampere
scale. It is a motor-load proxy: acceleration, friction, binding and book
contact can all raise current. It does not directly measure clamping force.

PHYSICAL TORQUE ESTIMATE
The supplied GM6020 manual states a torque constant of 0.741 N*m/A but does
not specify feedback current counts per ampere. The documented +/-16384 to
+/-3 A mapping is for CURRENT COMMANDS, not a verified feedback conversion.
Do not apply that command mapping to feedback without verifying it.
If the feedback-to-current scale is verified as S amperes/count, configure
set6020TorqueCalibration(1, 2, 0.741f * S, zero_current_offset_raw).
Alternatively calibrate N*m/count using known applied torque and appropriate
instrumentation. get6020Torque(1, 2, &torque_nm) then returns signed estimated
motor electromagnetic torque. Until calibrated, it returns false. Offline
feedback also returns false. Calibration is in RAM and must be set at startup.
This estimate is not a load-cell measurement and does not account for gearbox
ratio, transmission efficiency, or friction. Output is left untouched on error.

ONE CLOSURE
const float pid[3] = {20.0f, 0.0f, 0.0f}; /* illustrative, tune on mechanism */
GM6020_ClampConfig config = {
    .threshold_raw = contact_threshold_raw, /* measured experimentally */
    .confirm_ms = 3,                        /* illustrative, tune */
    .max_run_ms = 2000,                     /* illustrative closure deadline */
    .command_limit = 1000                   /* illustrative voltage units */
};
if (!start6020Clamp(1, 2, 10.0f, pid, &config)) {
    /* invalid config, no fresh feedback, or existing load >= threshold */
}
/* In later task iterations: */
GM6020_ClampStatus status;
if (get6020ClampStatus(1, 2, &status)) {
    if (status.state == GM6020_CLAMP_CONTACT) {
        /* drive is disabled; closure detected */
    }
    /* TIMEOUT, FEEDBACK_LOST, SERVICE_LATE and STOPPED are separate outcomes. */
}

HOW TO CHOOSE THE CONTACT THRESHOLD
1. Run at a slow closing speed with a low output limit. Measure abs(raw current)
   throughout unloaded motion, including acceleration and ordinary friction.
2. Measure contact current using a compliant test object and a reference force
   measurement if book pressure must be quantified. Set the threshold above
   normal motion load and below the level that applies excessive pressure.
3. Verify across thick/thin books, both travel directions if used, and the
   full mechanism range. Keep speed low so inertia and acceleration current
   do not dominate contact detection. Increase command limit only as needed.
4. Tune confirmation time against real feedback noise and stopping distance.
   Longer confirmation rejects noise but allows extra compression. Zero means
   stop on the first threshold-crossing feedback frame. There is no startup
   blanking interval: high startup current may legitimately trigger a stop.
5. If normal friction and light-contact current overlap, current detection
   cannot reliably distinguish them. Use a force sensor, compliant mechanism,
   or mechanical slip limiter rather than increasing the threshold blindly.

STOP BEHAVIOR
Threshold uses abs(raw current) directly, independent of torque calibration.
The peak magnitude observed during closure is recorded in peak_current_raw.
A continuous crossing must last confirm_ms with new feedback samples; a drop
below threshold or feedback gap >20 ms resets confirmation. It does not wait
for a local peak, which would be detected only after the load has fallen.
CONTACT is latched from the CAN receive path; next successful Service TX sends
zero. Typical software delay is confirmation time plus up to one service period,
but CAN congestion/errors can extend it. Existing queued frames can precede
zero; this is not an instantaneous mechanical halt. Rotor inertia can continue
motion. Zero drive does not brake, hold torque, or maintain clamp pressure;
the mechanism may backdrive or release. This implements your requested stop
of drive on contact, not an active holding mode.
Nonzero set6020RPM calls are rejected during closure and after any guarded
stop. stop6020 or a zero-RPM call always stops but does not clear the latch.
Only a successful start6020Clamp explicitly rearms the closure. Do not call
it repeatedly in the task loop or ignore fault outcomes.

The output is bounded by command_limit with matching integral anti-windup.
Voltage mode units are raw voltage command units (maximum 25000), not a direct
physical torque cap. Current mode maximum is 16384; motor Assistant settings
must match the bus mode. Same raw command limit has different meaning in each
mode. If gentle-force limits matter, configure verified current control or a
physical limiter; the software current detector alone cannot guarantee pressure.

Fresh feedback is required to start. A feedback age >=100 ms, service gap >20
ms, or elapsed max_run_ms latches a zero-drive stop. A bus failure can prevent
zero reaching the motor. Fault cases do not auto-resume on feedback recovery.
confirm_ms must be less than max_run_ms; maximum max_run_ms is 0x7fffffff.
Threshold range is 1..32768 including magnitude of signed -32768 feedback.
All APIs are task-context only except GM6020_OnRx; the existing FreeRTOS
critical sections protect shared state. No allocations or blocking waits.

VALIDATION
Existing protocol/PID tests and added torque/closure tests passed, including
calibration, signed feedback, transient rejection, sustained threshold, command
limit, latched restart rejection, explicit rearm, closure timeout, lost feedback,
delayed service, bus 2, int16 minimum, fresh-frame confirmation and tick wrap.
Complete STM32F407 firmware compiled and linked against the actual HAL and
FreeRTOS sources. Hardware calibration and book-clamping tests remain to do.

MAIN.C CLAMP HELPER
RunBookClamp(float closingRPM) is a static nonblocking helper outside MainTask.
Example: RunBookClamp(-60.0f). It uses CAN1 motor 2, PID {55,0.01,0},
raw threshold 2500, confirm 5 ms, timeout 3000 ms, and command limit 10000.
It calls start6020Clamp once and updates debugger start/target state.
LibraryHandler continues RPM regulation until a threshold/fault stop occurs.
Do not call RunBookClamp continuously: each successful call rearms the guard.
UpdateBookClampStatus refreshes debugger feedback; WaitForBookClampFeedback
handles the initial bounded wait. Button state, debounce, and nextRPM toggle
remain entirely in MainTask. These helpers are local to main.c.
