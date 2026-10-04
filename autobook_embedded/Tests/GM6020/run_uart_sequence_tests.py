from pathlib import Path
import os
import shutil
import subprocess
import tempfile

project = Path(__file__).resolve().parents[2]
source = (project / "Core/Src/main.c").read_text()


def section(name):
    start = source.index("/* USER CODE BEGIN " + name + " */")
    end = source.index("/* USER CODE END " + name + " */", start)
    return source[start:end]


def function(name):
    start = source.index("void " + name + "(void *argument)\n{")
    end = source.index("\n}\n", start) + 3
    return source[start:end]


start = source.index("/* Book clamp helpers:")
end = source.index("void HAL_CAN_RxFifo0MsgPendingCallback", start)
helpers = source[start:end]

pre = r'''#include "gm6020.h"
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
static uint32_t tick;
static unsigned starts, stops, entries, pwm_starts, uartIterations, uartLimit;
static unsigned statusCalls, faultOnStatusCall;
static GM6020_ClampState forcedState = GM6020_CLAMP_RUNNING;
static bool statusValid = true, startAllowed = true, stopAllowed = true;
static bool motorEnabled, uartMode, failTx;
static uint16_t pulses[4], history[32][4];
static uint32_t times[32];
static char transmitted[4096];
static size_t transmittedLength;
static jmp_buf done;
static int htim1;
static uint16_t motorPos;
static void feed(const char*);
#define __HAL_TIM_SET_COMPARE(timer,channel,value) compare(channel,value)
static void compare(unsigned channel,uint16_t value){
  assert(channel<4);
  if(entries>=2)assert(!motorEnabled);
  pulses[channel]=value;
  if(channel==3){
    assert(entries<32);
    for(unsigned i=0;i<4;++i)history[entries][i]=pulses[i];
    times[entries++]=tick;
  }
}
uint32_t HAL_GetTick(void){return tick;}
void osDelay(unsigned delay){
  if(uartMode){if(++uartIterations>=uartLimit)longjmp(done,1);return;}
  tick+=delay;
  if(tick==100)feed("50 42\n");
  if(tick==300)feed("50 43\r\n");
  if(tick>=6000)longjmp(done,1);
}
void Error_Handler(void){assert(0);}
HAL_StatusTypeDef HAL_UART_Receive_IT(UART_HandleTypeDef *u,uint8_t *data,uint16_t length){
  assert(u==&huart1&&data&&length==1);return HAL_OK;
}
HAL_StatusTypeDef HAL_UART_Transmit(UART_HandleTypeDef *u,uint8_t *data,uint16_t length,uint32_t timeout){
  assert(u==&huart1&&data&&timeout==10);
  size_t sent=failTx && length>4 ? 4 : length;
  assert(transmittedLength+sent<sizeof(transmitted));
  memcpy(transmitted+transmittedLength,data,sent);
  transmittedLength+=sent;transmitted[transmittedLength]=0;
  if(failTx){failTx=false;return HAL_ERROR;}
  return HAL_OK;
}
HAL_StatusTypeDef HAL_TIM_PWM_Start(int *timer,unsigned channel){
  (void)timer;assert(channel==pwm_starts++);assert(entries==1);return HAL_OK;
}
int HAL_GPIO_ReadPin(void *port,unsigned pin){
  (void)port;(void)pin;return !(tick>=600&&tick<700);
}
uint16_t get6020Pos(uint8_t b,uint8_t m){assert(b==1&&m==2);return 42;}
bool get6020Feedback(uint8_t b,uint8_t m,GM6020_Feedback *f){
  assert(b==1&&m==2);f->online=true;f->current_raw=0;return true;
}
bool start6020Clamp(uint8_t b,uint8_t m,float rpm,const float pid[3],const GM6020_ClampConfig *c){
  assert(b==1&&m==2&&rpm==60);assert(pid[0]==55&&c->threshold_raw==2500);
  ++starts;motorEnabled=startAllowed;return startAllowed;
}
bool stop6020(uint8_t b,uint8_t m){
  assert(b==1&&m==2);++stops;motorEnabled=false;return stopAllowed;
}
bool get6020ClampStatus(uint8_t b,uint8_t m,GM6020_ClampStatus *s){
  assert(b==1&&m==2);
  if(++statusCalls==faultOnStatusCall)forcedState=GM6020_CLAMP_SERVICE_LATE;
  s->state=forcedState;s->peak_current_raw=2500;return statusValid;
}
'''

end = r'''static void feed(const char *s){
  for(;*s;++s){bookUartRxByte=(uint8_t)*s;HAL_UART_RxCpltCallback(&huart1);}
}
static void runUart(unsigned iterations){
  uartMode=true;uartIterations=0;uartLimit=iterations;
  if(!setjmp(done))StartUartTask(NULL);
  uartMode=false;
}
static void clearTx(void){transmittedLength=0;transmitted[0]=0;}
static void reset(void){
  bookSequenceActive=false;bookSequenceStartRejected=false;
  bookUartStatusHead=bookUartStatusTail=bookUartStatusCount=0;
  bookUartTxNeedsResync=false;bookUartRxHead=bookUartRxTail=0;
  bookUartRxOverflow=bookUartRxNeedsRearm=false;
  starts=stops=entries=pwm_starts=statusCalls=faultOnStatusCall=0;
  tick=0;motorEnabled=false;startAllowed=stopAllowed=statusValid=true;
  forcedState=GM6020_CLAMP_RUNNING;failTx=false;clearTx();
}
static void command(uint32_t id){
  assert(CanReadBookCommand());
  const BookUartCommand value={50,id};HandleBookCommand(&value);
}
static void finishSequence(void){
  const uint32_t delays[]={BOOK_STATE_2_TO_3_MS,BOOK_STATE_3_TO_4_MS,BOOK_STATE_4_TO_5_MS,BOOK_STATE_5_TO_6_MS};
  for(unsigned i=0;i<4;++i){
    tick=bookSequenceStateStartedMs+delays[i]-1;
    ServiceBookSequence(tick);assert(bookDeviceState==(BookDeviceState)(i+2)&&bookSequenceActive);
    tick=bookSequenceStateStartedMs+delays[i];
    ServiceBookSequence(tick);assert(bookDeviceState==(BookDeviceState)(i+3));
  }
  assert(!bookSequenceActive);
}
int main(void){
  BookUartCommand v;
  feed("5");assert(!ReadUartCommand(&v));feed("0\r\n");
  assert(ReadUartCommand(&v)&&v.command==50&&v.requestId==0);assert(!ReadUartCommand(&v));
  feed("4294967295 4294967295\n");assert(ReadUartCommand(&v)&&v.command==UINT32_MAX&&v.requestId==UINT32_MAX);
  feed("50 42\r\n");assert(ReadUartCommand(&v)&&v.command==50&&v.requestId==42);assert(!ReadUartCommand(&v));
  feed("4294967296\n50 4294967296\n50x\n-50\n50 \n50  1\n 50\n50 1x\n");
  assert(!ReadUartCommand(&v));
  feed("12\n50\n");assert(ReadUartCommand(&v)&&v.command==12);assert(ReadUartCommand(&v)&&v.command==50);
  for(unsigned i=0;i<200;++i)feed("1");assert(!ReadUartCommand(&v));
  feed("\n50\n");assert(ReadUartCommand(&v)&&v.command==50);
  HAL_UART_ErrorCallback(&huart1);assert(bookUartRxErrors==1);assert(!ReadUartCommand(&v));
  feed("\n50\n");assert(ReadUartCommand(&v)&&v.command==50);assert(!ReadUartCommand(&v));
  UART_HandleTypeDef other;bookUartRxByte='5';HAL_UART_RxCpltCallback(&other);assert(!ReadUartCommand(&v));

  /* The invalid marker must cancel partial legacy commands before its newline. */
  const char *fragments[]={"50","50 ","50 1234"};
  for(unsigned parsedFragment=0;parsedFragment<2;++parsedFragment){
    for(unsigned i=0;i<sizeof(fragments)/sizeof(fragments[0]);++i){
      reset();feed("\n");assert(!ReadUartCommand(&v));feed(fragments[i]);
      if(parsedFragment)assert(!ReadUartCommand(&v));
      feed("!\n50 77\n");
      assert(ReadUartCommand(&v)&&v.command==50&&v.requestId==77);
      HandleBookCommand(&v);assert(starts==1&&bookSequenceRequestId==77);
      assert(!ReadUartCommand(&v));runUart(1);
      assert(strcmp(transmitted,"ACK 77\r\n")==0);
    }
  }
  reset();feed("50 11\n");assert(ReadUartCommand(&v)&&v.requestId==11);HandleBookCommand(&v);
  feed("!\n50 77\n");assert(ReadUartCommand(&v)&&v.requestId==77);HandleBookCommand(&v);
  assert(!ReadUartCommand(&v)&&starts==1&&bookSequenceRequestId==11);
  runUart(2);assert(strcmp(transmitted,"ACK 11\r\nBUSY 77\r\n")==0);
  for(unsigned error=0;error<2;++error){
    reset();
    if(error){feed("50 12");HAL_UART_ErrorCallback(&huart1);}
    else for(unsigned i=0;i<200;++i)feed("1");
    assert(!ReadUartCommand(&v));feed("!\n50 77\n");
    assert(ReadUartCommand(&v)&&v.command==50&&v.requestId==77);
    HandleBookCommand(&v);assert(starts==1&&!ReadUartCommand(&v));
  }

  reset();if(!setjmp(done))StartMainTask(NULL);
  const uint16_t expected[6][4]={{2100,1200,1800,800},{2100,1600,1400,800},{1000,1600,1400,800},{1000,1600,1400,1800},{1000,1600,2100,1800},{1000,1600,1400,1800}};
  assert(entries==6&&starts==1&&pwm_starts==4);
  const uint32_t expectedTimes[6]={0,100,100+BOOK_STATE_2_TO_3_MS,100+BOOK_STATE_2_TO_3_MS+BOOK_STATE_3_TO_4_MS,100+BOOK_STATE_2_TO_3_MS+BOOK_STATE_3_TO_4_MS+BOOK_STATE_4_TO_5_MS,100+BOOK_STATE_2_TO_3_MS+BOOK_STATE_3_TO_4_MS+BOOK_STATE_4_TO_5_MS+BOOK_STATE_5_TO_6_MS};
  for(unsigned j=0;j<6;++j){for(unsigned i=0;i<4;++i)assert(history[j][i]==expected[j][i]);assert(times[j]==expectedTimes[j]);}
  assert(bookDeviceState==BOOK_STATE_6&&!bookSequenceActive&&!bookSequenceStartRejected);
  assert(bookUartStatusCount==3);
  /* A partial transmit retains ACK and repairs the line before retrying it. */
  failTx=true;runUart(1);assert(bookUartStatusCount==3&&bookUartTxNeedsResync);
  runUart(3);assert(bookUartStatusCount==0&&!bookUartTxNeedsResync);
  assert(strcmp(transmitted,"ACK \r\nACK 42\r\nBUSY 43\r\nDONE 42\r\n")==0);
  clearTx();motorPos=42;runUart(1);assert(strcmp(transmitted,"42\r\n")==0);
  clearTx();motorPos=GM6020_INVALID_POSITION;runUart(1);assert(strcmp(transmitted,"offline\r\n")==0);
  failTx=true;runUart(1);assert(bookUartTxNeedsResync);
  command(UINT32_MAX);clearTx();runUart(1);
  assert(strcmp(transmitted,"\r\nACK 4294967295\r\n")==0);

  reset();startAllowed=false;command(7);
  assert(!bookSequenceActive&&bookSequenceStartRejected);
  ServiceBookSequence(999999);runUart(1);
  assert(strcmp(transmitted,"ERROR 7 START_REJECTED\r\n")==0);
  const GM6020_ClampState faults[]={GM6020_CLAMP_FEEDBACK_LOST,GM6020_CLAMP_TIMEOUT,GM6020_CLAMP_SERVICE_LATE};
  const char *errors[]={"ACK 8\r\nERROR 8 FEEDBACK_LOST\r\n","ACK 8\r\nERROR 8 TIMEOUT\r\n","ACK 8\r\nERROR 8 SERVICE_LATE\r\n"};
  for(unsigned i=0;i<3;++i){
    reset();command(8);forcedState=faults[i];tick=1;ServiceBookSequence(tick);
    assert(!bookSequenceActive&&!motorEnabled&&bookDeviceState==BOOK_STATE_2);
    tick=100000;ServiceBookSequence(tick);runUart(2);assert(strcmp(transmitted,errors[i])==0);
  }
  reset();command(8);statusValid=false;ServiceBookSequence(0);runUart(2);
  assert(strcmp(transmitted,errors[0])==0);
  reset();command(8);faultOnStatusCall=2;tick=BOOK_STATE_2_TO_3_MS;ServiceBookSequence(tick);runUart(2);
  assert(!bookSequenceActive&&bookDeviceState==BOOK_STATE_2&&strcmp(transmitted,errors[2])==0);
  reset();command(9);stopAllowed=false;tick=BOOK_STATE_2_TO_3_MS;ServiceBookSequence(tick);runUart(2);
  assert(!bookSequenceActive&&strcmp(transmitted,"ACK 9\r\nERROR 9 STOP_REJECTED\r\n")==0);

  /* Saturated BUSY replies retain capacity for DONE, then TX drains FIFO. */
  reset();command(10);
  for(unsigned i=0;i<BOOK_UART_STATUS_CAPACITY-2;++i)command(100+i);
  assert(bookUartStatusCount==BOOK_UART_STATUS_CAPACITY-1&&!CanReadBookCommand());
  feed("50 99\n");
  finishSequence();assert(bookUartStatusCount==BOOK_UART_STATUS_CAPACITY);
  runUart(BOOK_UART_STATUS_CAPACITY);assert(bookUartStatusCount==0&&CanReadBookCommand());
  assert(strncmp(transmitted,"ACK 10\r\nBUSY 100\r\n",18)==0);
  assert(strcmp(transmitted+transmittedLength-9,"DONE 10\r\n")==0);
  assert(ReadUartCommand(&v)&&v.requestId==99);HandleBookCommand(&v);
  assert(bookSequenceActive&&bookSequenceRequestId==99);

  /* Contact permits timer advancement; wrap and delayed polls preserve pacing. */
  reset();tick=UINT32_MAX-500;command(0);forcedState=GM6020_CLAMP_CONTACT;
  finishSequence();runUart(2);assert(strcmp(transmitted,"ACK 0\r\nDONE 0\r\n")==0);
  reset();command(1);tick=10000;ServiceBookSequence(tick);
  assert(bookDeviceState==BOOK_STATE_3&&bookSequenceStateStartedMs==10000);
  tick+=BOOK_STATE_3_TO_4_MS-1;ServiceBookSequence(tick);assert(bookDeviceState==BOOK_STATE_3);
  puts("PASS: actual MainTask/UartTask, correlated replies, calibrated completion timing, parser recovery, clamp faults, queue saturation, partial TX recovery, telemetry, wrap and delayed polling");
}
'''

compiler = os.environ.get("CC") or shutil.which("gcc") or shutil.which("clang")
if not compiler:
    raise RuntimeError("Set CC to a host C compiler, or install gcc/clang.")

for definitions in (
    section("PD"),
    section("PD")
    .replace("BOOK_STATE_3_TO_4_MS 1000U", "BOOK_STATE_3_TO_4_MS 500U")
    .replace("BOOK_STATE_4_TO_5_MS 1000U", "BOOK_STATE_4_TO_5_MS 1500U")
    .replace("BOOK_STATE_5_TO_6_MS 1000U", "BOOK_STATE_5_TO_6_MS 750U"),
):
    with tempfile.TemporaryDirectory() as temporary:
        work = Path(temporary)
        (work / "test.c").write_text(
            pre + section("PTD") + definitions + section("PV") + helpers
            + function("StartMainTask") + function("StartUartTask") + end
        )
        subprocess.run(
            [
                compiler, "-std=c11", "-Wall", "-Wextra", "-Werror",
                "-Wno-misleading-indentation",
                "-I" + str(project / "Tests/GM6020"),
                "-I" + str(project / "Core/Inc"),
                str(work / "test.c"), "-o", str(work / "test.exe"),
            ],
            check=True,
        )
        subprocess.run([str(work / "test.exe")], check=True)
