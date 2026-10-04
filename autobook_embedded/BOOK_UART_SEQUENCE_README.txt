UART COMMAND AND TIMER-ONLY STATE SEQUENCE

Send ASCII 50 followed by Enter (CR, LF or CRLF) at 115200 baud, 8N1.
The device enters state 2 immediately, then advances 2 -> 3 -> 4 -> 5 -> 6
using timers only. It remains in state 6 on completion. Another 50 command
starts another run. Button presses and repeated commands are ignored while
an automatic sequence is active. Manual stepping remains available when idle.

Tune these four constants at the top of main.c, in USER CODE PD:
#define BOOK_STATE_2_TO_3_MS 1000U
#define BOOK_STATE_3_TO_4_MS 1000U
#define BOOK_STATE_4_TO_5_MS 1000U
#define BOOK_STATE_5_TO_6_MS 1000U
Each delay is measured from actual entry into its source state. Units are ms.
Timing is nonblocking and wrap-safe. A late task advances one state and starts
its next full interval; states are not skipped to catch up. Zero delay advances
on the next polling iteration (MainTask polls every 5 ms).

Motor behavior is independent of transition timing. State 2 starts the existing
guarded +60 RPM clamp. The current threshold may stop it early but never causes
a state transition. State 3 commands zero drive before its servo compare
updates. States 4, 5 and 6 also command zero before their servo updates.
Stop is transmitted by the next successful library CAN service; zero drive
is not physical braking and rotor motion may continue under inertia.
Increase BOOK_STATE_3_TO_4_MS if more settling time is needed before state 4.
If state 2 cannot start the clamp, the automatic sequence is not activated;
bookSequenceStartRejected reports that outcome. The old contact-specific
bookSequenceFailed / bookSequenceFailureReason fields were removed.

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

ReadUartValue is a nonblocking unsigned-decimal line parser. Interrupt RX uses
a 128-byte ring. Malformed/overflowed commands are discarded; no partial
command is executed. Commands without a line ending wait for completion.
Use the Type C UART2-labelled connector (STM32 USART1): converter TX -> PB7/RX,
converter RX -> PA9/TX, common GND and compatible TTL logic levels.
UART position transmission and the 1 ms motor service continue independently.

Watch bookDeviceState, bookSequenceActive, bookSequenceStartRejected,
bookLastUartCommand, bookStateActionAccepted and clampExample* in the debugger.

Validation: full firmware compile/link passed. Tests execute the actual parser,
MainTask and helpers with both equal delays and unequal delays (1000, 500,
1500, 750 ms). Verified independent timing, stop-before-servo ordering, UART
parsing/recovery, ignored commands/buttons, tick wrap and delayed task handling.
No hardware mechanical or UART verification was performed.
