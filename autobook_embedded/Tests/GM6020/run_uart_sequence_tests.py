from pathlib import Path
import subprocess, tempfile
p=Path(__file__).resolve().parents[2]
s=(p/'Core/Src/main.c').read_text()
a=s.index('void StartMainTask(void *argument)\n{');b=s.index('\n}\n',a)+3
body=s[a:b]
a=s.index('/* Book clamp helpers:');b=s.index('void HAL_CAN_RxFifo0MsgPendingCallback',a)
helpers=s[a:b]
def section(name):
    a=s.index('/* USER CODE BEGIN '+name+' */');b=s.index('/* USER CODE END '+name+' */',a)
    return s[a:b]
pre=r'''#include "gm6020.h"
#include <assert.h>
#include <setjmp.h>
#include <stdio.h>
#include <string.h>
#define taskENTER_CRITICAL() ((void)0)
#define taskEXIT_CRITICAL() ((void)0)
#define GPIOA ((void*)3)
#define GPIO_PIN_0 1
#define GPIO_PIN_RESET 0
#define TIM_CHANNEL_1 0
#define TIM_CHANNEL_2 1
#define TIM_CHANNEL_3 2
#define TIM_CHANNEL_4 3
typedef struct {unsigned id;} UART_HandleTypeDef;
static UART_HandleTypeDef huart1;
static unsigned tick, starts, stops, entries, pwm_starts;
static GM6020_ClampState forcedState = GM6020_CLAMP_IDLE;
static bool statusValid = true;
static bool motorEnabled;
static uint16_t pulses[4], history[16][4];static unsigned times[16];
static jmp_buf done;
static int htim1;
static uint16_t motorPos;
static void feed(const char*);
#define __HAL_TIM_SET_COMPARE(timer,channel,value) compare(channel,value)
static void compare(unsigned channel,uint16_t value){assert(channel<4);if(entries>=2)assert(!motorEnabled);pulses[channel]=value;if(channel==3){assert(entries<16);for(unsigned i=0;i<4;++i)history[entries][i]=pulses[i];times[entries++]=tick;}}
uint32_t HAL_GetTick(void){return tick;}
void osDelay(unsigned delay){tick+=delay;if(tick==100)feed("50\n");if(tick==300)feed("50\r\n");if(tick>=5000)longjmp(done,1);}
void Error_Handler(void){assert(0);}
HAL_StatusTypeDef HAL_UART_Receive_IT(UART_HandleTypeDef *u,uint8_t *data,uint16_t length){assert(u==&huart1&&data&&length==1);return HAL_OK;}
HAL_StatusTypeDef HAL_TIM_PWM_Start(int *timer,unsigned channel){(void)timer;assert(channel==pwm_starts++);assert(entries==1);return HAL_OK;}
int HAL_GPIO_ReadPin(void *port,unsigned pin){(void)port;(void)pin;return !(tick>=600&&tick<700);}
uint16_t get6020Pos(uint8_t b,uint8_t m){assert(b==1&&m==2);return 42;}
bool get6020Feedback(uint8_t b,uint8_t m,GM6020_Feedback *f){assert(b==1&&m==2);f->online=true;f->current_raw=0;return true;}
bool start6020Clamp(uint8_t b,uint8_t m,float rpm,const float pid[3],const GM6020_ClampConfig *c){assert(b==1&&m==2&&rpm==60);assert(pid[0]==55&&c->threshold_raw==2500);++starts;motorEnabled=true;return true;}
bool stop6020(uint8_t b,uint8_t m){assert(b==1&&m==2);++stops;motorEnabled=false;return true;}
bool get6020ClampStatus(uint8_t b,uint8_t m,GM6020_ClampStatus *s){(void)b;(void)m;s->state=forcedState != GM6020_CLAMP_IDLE ? forcedState : (tick < 1600 ? GM6020_CLAMP_RUNNING : GM6020_CLAMP_CONTACT);s->peak_current_raw=2500;return statusValid;}
'''
end=r'''static void feed(const char *s){for(;*s;++s){bookUartRxByte=(uint8_t)*s;HAL_UART_RxCpltCallback(&huart1);}}
int main(void){uint32_t v;
feed("5");assert(!ReadUartValue(&v));feed("0\r\n");assert(ReadUartValue(&v)&&v==50);assert(!ReadUartValue(&v));
feed("4294967295\n");assert(ReadUartValue(&v)&&v==UINT32_MAX);
feed("4294967296\n50x\n-50\n\n");assert(!ReadUartValue(&v));
feed("12\n50\n");assert(ReadUartValue(&v)&&v==12);assert(ReadUartValue(&v)&&v==50);
for(unsigned i=0;i<200;++i)feed("1");assert(!ReadUartValue(&v));feed("\n50\n");assert(ReadUartValue(&v)&&v==50);
HAL_UART_ErrorCallback(&huart1);assert(bookUartRxErrors==1);assert(!ReadUartValue(&v));feed("\n50\n");assert(ReadUartValue(&v)&&v==50);assert(!ReadUartValue(&v));
/* Callback ignores other UART peripherals. */
UART_HandleTypeDef other;bookUartRxByte='5';HAL_UART_RxCpltCallback(&other);assert(!ReadUartValue(&v));
if(!setjmp(done))StartMainTask(NULL);
const uint16_t expected[6][4]={{2100,1200,1800,800},{2100,1600,1400,800},{1000,1600,1400,800},{1000,1600,1400,1800},{1000,1600,2100,1800},{1000,1600,1400,1800}};
assert(entries==6&&starts==1&&pwm_starts==4);
const unsigned expectedTimes[6]={0,100,100+BOOK_STATE_2_TO_3_MS,100+BOOK_STATE_2_TO_3_MS+BOOK_STATE_3_TO_4_MS,100+BOOK_STATE_2_TO_3_MS+BOOK_STATE_3_TO_4_MS+BOOK_STATE_4_TO_5_MS,100+BOOK_STATE_2_TO_3_MS+BOOK_STATE_3_TO_4_MS+BOOK_STATE_4_TO_5_MS+BOOK_STATE_5_TO_6_MS};
for(unsigned j=0;j<6;++j){for(unsigned i=0;i<4;++i)assert(history[j][i]==expected[j][i]);assert(times[j]==expectedTimes[j]);}
assert(bookDeviceState==BOOK_STATE_6&&!bookSequenceActive&&!bookSequenceStartRejected);
/* Subsequent explicit command can run the sequence again. */
feed("50\n");assert(ReadUartValue(&v)&&v==50);
/* Contact/fault status does not affect timer-only advancement. */
entries=0;EnterBookState(BOOK_STATE_2);bookSequenceActive=true;
forcedState=GM6020_CLAMP_CONTACT;statusValid=false;bookSequenceStateStartedMs=0;
tick=BOOK_STATE_2_TO_3_MS-1;ServiceBookSequence(tick);assert(bookDeviceState==BOOK_STATE_2);
forcedState=GM6020_CLAMP_RUNNING;tick=BOOK_STATE_2_TO_3_MS;ServiceBookSequence(tick);assert(bookDeviceState==BOOK_STATE_3&&!motorEnabled);
/* Timing is wrap-safe and a late task advances only one stage. */
bookSequenceStateStartedMs=UINT32_MAX-500;
tick=(uint32_t)(bookSequenceStateStartedMs+BOOK_STATE_3_TO_4_MS-1);ServiceBookSequence(tick);assert(bookDeviceState==BOOK_STATE_3);
tick=(uint32_t)(bookSequenceStateStartedMs+BOOK_STATE_3_TO_4_MS);ServiceBookSequence(tick);assert(bookDeviceState==BOOK_STATE_4);
tick+=10000;ServiceBookSequence(tick);assert(bookDeviceState==BOOK_STATE_5);
puts("PASS: numeric UART parser, partial/CRLF input, overflow/errors, timer-only transitions, independent delays, motor stop ordering, repeated command/button ignored, tick wrap, delayed-task behavior");}
'''
# These source strings use escaped newlines; make the C string escapes single.
with tempfile.TemporaryDirectory() as temporary:
    w=Path(temporary)
    (w/'test.c').write_text(pre+section('PTD')+section('PD')+section('PV')+helpers+body+end)
    subprocess.run(['gcc','-std=c11','-Wall','-Wextra','-Werror','-Wno-misleading-indentation','-I'+str(p/'Tests/GM6020'),'-I'+str(p/'Core/Inc'),str(w/'test.c'),'-o',str(w/'test.exe')],check=True)
    subprocess.run([str(w/'test.exe')],check=True)


# Recompile the same actual control flow with unequal delays to verify each knob.
with tempfile.TemporaryDirectory() as temporary:
    w=Path(temporary)
    tuned=section('PD').replace('BOOK_STATE_3_TO_4_MS 1000U','BOOK_STATE_3_TO_4_MS 500U').replace('BOOK_STATE_4_TO_5_MS 1000U','BOOK_STATE_4_TO_5_MS 1500U').replace('BOOK_STATE_5_TO_6_MS 1000U','BOOK_STATE_5_TO_6_MS 750U')
    (w/'test.c').write_text(pre+section('PTD')+tuned+section('PV')+helpers+body+end)
    subprocess.run(['gcc','-std=c11','-Wall','-Wextra','-Werror','-Wno-misleading-indentation','-I'+str(p/'Tests/GM6020'),'-I'+str(p/'Core/Inc'),str(w/'test.c'),'-o',str(w/'test.exe')],check=True)
    subprocess.run([str(w/'test.exe')],check=True)
