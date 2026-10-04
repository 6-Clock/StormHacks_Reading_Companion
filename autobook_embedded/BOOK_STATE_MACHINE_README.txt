BOOK DEVICE STATE MACHINE

MainTask starts in BOOK_STATE_1 and advances on each debounced PA0 press:
1 -> 2 -> 3 -> 1 -> 2 ...
PA0 is active LOW with pull-up. Press and release each need 30 ms stability.
Holding the button (including at boot) does not repeatedly advance states.

State   PWM CH1  CH2   CH3   CH4    GM6020
1       1000     1000  2000  2000   Zero drive
2       1000     1300  1700  2000   Guarded +60 RPM
3       1000     1300  2000  1000   Zero drive

TIM1's 1 us tick means the PWM values are pulse widths in microseconds.
State 1 compare values are loaded before all four PWM channels are enabled.
PWM pin mapping remains PE9/PE11/PE13/PE14 as configured in your project.

EnterBookState applies servo outputs once and starts/stops the GM6020.
RunBookClamp retains PID {55,0.01,0}, raw-current threshold 2500, confirmation
5 ms, maximum run 3000 ms, and output command cap 10000 on CAN1 motor 2.
The motor begins only when entering state 2. Threshold/fault stops do not
advance device state and do not automatically rearm. Pressing the button in
state 2 enters state 3 and stops motor drive even if contact has not occurred.
These pulses are commanded together; there is no servo settling delay before
starting the clamp. Zero drive does not actively brake or hold the clamp.

Watch bookDeviceState and bookStateActionAccepted in the debugger.
State 2 is still selected if clamp start is rejected, with its servo settings
applied. bookStateActionAccepted=false / clampExampleStartRejected=true
report that no clamp run began. No automatic retries; cycle through 3 and 1
and press again to retry state 2 after feedback/load is suitable.
Existing clampExample* watches show raw load and the clamp stop reason.
Button handling stays in MainTask and clamping logic stays in its helper.

Complete firmware compile/link passed. Tests execute actual MainTask and
state-entry helpers with simulated button input, verifying every output,
positive clamping, stop states, wrap cycle, bounce/hold behavior, rejected
starts, and initial PWM loading. Hardware operation remains unverified.
