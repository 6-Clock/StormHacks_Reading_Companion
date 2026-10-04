UART COMPLETION PROTOCOL AND TIMER-ONLY STATE SEQUENCE

Send ASCII "50 <request_id>" followed by CR, LF or CRLF at 115200 baud, 8N1.
The request ID is an unsigned 32-bit decimal value. Legacy "50" remains valid
and uses request ID 0. Replies are ASCII lines terminated by CRLF:
ACK <request_id>                         Accepted and sequence started.
DONE <request_id>                        Existing timed sequence reached state 6.
BUSY <request_id>                        Another automatic sequence is active.
ERROR <request_id> <reason>              Start rejected or sequence aborted.
Reasons are START_REJECTED, FEEDBACK_LOST, TIMEOUT, SERVICE_LATE,
STOP_REJECTED or INVALID_STATE. A rejected/aborted command never sends DONE.
Each accepted command sends ACK then one terminal DONE or ERROR reply.
Numeric GM6020 position lines and "offline" telemetry may appear between
replies. Hosts must match request IDs and ignore unrelated telemetry.

The device enters state 2 immediately, then advances 2 -> 3 -> 4 -> 5 -> 6
using timers only. It remains in state 6 on completion. Another 50 command
starts another run. Button presses are ignored while an automatic sequence
is active; repeated commands receive BUSY with their own request IDs.
Manual stepping remains available when idle. DONE is emitted on entry into
state 6 after its outputs are applied, using the existing calibrated delays.
No additional settling delay is added. This is timer-based completion;
the PWM servos do not provide measured position feedback to this firmware.

Tune these four constants at the top of main.c, in USER CODE PD:
#define BOOK_STATE_2_TO_3_MS 1000U
#define BOOK_STATE_3_TO_4_MS 1000U
#define BOOK_STATE_4_TO_5_MS 1000U
#define BOOK_STATE_5_TO_6_MS 1000U
Each delay is measured from actual entry into its source state. Units are ms.
Timing is nonblocking and wrap-safe. A late task advances one state and starts
its next full interval; states are not skipped to catch up. Zero delay advances
on the next polling iteration (MainTask polls every 5 ms).

State 2 starts the existing guarded +60 RPM clamp. The current threshold may
stop it early but never causes a state transition. Feedback loss, closure
timeout, or late motor service abort the automatic sequence and send ERROR
instead of DONE. The motor status is checked before its scheduled stop so
the stop cannot hide a pending fault. State 3 commands zero drive before its servo compare
updates. States 4, 5 and 6 also command zero before their servo updates.
Stop is transmitted by the next successful library CAN service; zero drive
is not physical braking and rotor motion may continue under inertia.
Increase BOOK_STATE_3_TO_4_MS if more settling time is needed before state 4.
If state 2 cannot start the clamp, the automatic sequence is not activated;
bookSequenceStartRejected reports that outcome and ERROR START_REJECTED is
sent with the rejected request ID.

Retained outputs:
State  PWM1  PWM2  PWM3  PWM4  GM6020
1      2100  1200  1800  800   Stop (initial)
2      2100  1600  1400  800   Guarded +60 RPM
3      1000  1600  1400  800   Stop
4      1000  1600  1400  1800  Stop
5      1000  1600  2100  1800  Stop
6      1000  1600  1400  1800  Stop
Clamp PID {55,0.01,0}, threshold 2500 raw, confirm 5 ms, maximum run 3000 ms,
and output cap 10000 remain unchanged on CAN1 motor 2.

ReadUartCommand is a nonblocking unsigned-decimal line parser. An optional
request ID follows exactly one ASCII space. Interrupt RX uses
a 128-byte ring. Malformed/overflowed commands are discarded; no partial
command is executed. Commands without a line ending wait for completion.
Use the Type C UART2-labelled connector (STM32 USART1): converter TX -> PB7/RX,
converter RX -> PA9/TX, common GND and compatible TTL logic levels.
UART position transmission and the 1 ms motor service continue independently.
Only UartTask transmits, preventing telemetry/status byte interleaving. A static
16-event queue transfers statuses from MainTask with short critical sections.
MainTask stops reading commands when fewer than two reply slots remain,
reserving capacity for an accepted sequence's terminal reply. Queued replies
take priority over telemetry and remain pending until HAL reports TX success.
After a failed TX a CRLF delimiter repairs any partial line before retrying.
Hosts should ignore blank/malformed lines and tolerate duplicate status lines
if a failed transmission reached the host before HAL reported failure.
Sustained TX failure backpressures command parsing; RX overflow then discards
complete corrupted input lines, and the host must time out without capture.

Watch bookDeviceState, bookSequenceActive, bookSequenceStartRejected,
bookLastUartCommand, bookStateActionAccepted and clampExample* in the debugger.

Host tests execute the actual parser, MainTask, UartTask and helpers with both
equal delays and unequal delays (1000, 500, 1500, 750 ms). They cover correlated
ACK/DONE/BUSY/ERROR replies, faults before transitions, queue saturation,
partial TX recovery, unchanged timing and stop ordering, malformed input,
RX recovery, telemetry, tick wrap and delayed task handling.
Run Tests/GM6020/run_uart_sequence_tests.py with Python and gcc or clang;
set CC to the host compiler path if needed. No hardware validation is implied.
