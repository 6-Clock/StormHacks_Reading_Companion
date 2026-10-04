from pathlib import Path
import subprocess, tempfile
p=Path(__file__).resolve().parents[2]
s=(p/'Core/Src/main.c').read_text()
a=s.index('void StartMainTask(void *argument)\n{');b=s.index('\n}\n',a)+3
body=s[a:b]
a=s.index('/* Book clamp helpers:');b=s.index('void HAL_CAN_RxFifo0MsgPendingCallback',a)
helpers=s[a:b]
a=s.index('/* USER CODE BEGIN PTD */');b=s.index('/* USER CODE END PTD */',a)
typedefs=s[a:b]
a=s.index('/* USER CODE BEGIN PV */');b=s.index('/* USER CODE END PV */',a)
globals=s[a:b]
pre=r'''#include "gm6020.h"
#include <assert.h>
#include <setjmp.h>
#include <stdio.h>
#define GPIOA ((void*)3)
#define GPIO_PIN_0 1
#define GPIO_PIN_RESET 0
#define TIM_CHANNEL_1 0
#define TIM_CHANNEL_2 1
#define TIM_CHANNEL_3 2
#define TIM_CHANNEL_4 3
static unsigned tick, scenario, starts, stops, entries, pwm_starts;
static uint16_t pulses[4], history[8][4];
static jmp_buf done;
static int htim1;
static uint16_t motorPos;
#define __HAL_TIM_SET_COMPARE(timer,channel,value) compare(channel,value)
static void compare(unsigned channel,uint16_t value){assert(channel<4);pulses[channel]=value;if(channel==3){assert(entries<8);for(unsigned i=0;i<4;++i)history[entries][i]=pulses[i];++entries;}}
uint32_t HAL_GetTick(void){return tick;}
void osDelay(unsigned delay){tick+=delay;if(tick>=700)longjmp(done,1);}
void Error_Handler(void){assert(0);}
HAL_StatusTypeDef HAL_TIM_PWM_Start(int *timer,unsigned channel){(void)timer;assert(channel==pwm_starts++);assert(entries==1);assert(pulses[0]==1000&&pulses[1]==1000&&pulses[2]==2000&&pulses[3]==2000);return HAL_OK;}
int HAL_GPIO_ReadPin(void *port,unsigned pin){(void)port;(void)pin;
if(scenario==2&&tick<60)return 0;
if((tick>=100&&tick<105)||(tick>=110&&tick<115)||(tick>=120&&tick<200)||
(tick>=260&&tick<340)||(tick>=400&&tick<470)||(tick>=520&&tick<650))return 0;
return 1;}
uint16_t get6020Pos(uint8_t b,uint8_t m){assert(b==1&&m==2);return 42;}
bool get6020Feedback(uint8_t b,uint8_t m,GM6020_Feedback *f){assert(b==1&&m==2);f->online=true;f->current_raw=0;return true;}
bool start6020Clamp(uint8_t b,uint8_t m,float rpm,const float pid[3],const GM6020_ClampConfig *c){
assert(b==1&&m==2&&rpm==60);assert(pid[0]==55&&pid[1]==0.01f&&pid[2]==0);
assert(c->threshold_raw==2500&&c->confirm_ms==5&&c->max_run_ms==3000&&c->command_limit==10000);
++starts;return !(scenario==1&&starts==1);}
bool stop6020(uint8_t b,uint8_t m){assert(b==1&&m==2);++stops;return true;}
bool get6020ClampStatus(uint8_t b,uint8_t m,GM6020_ClampStatus *s){(void)b;(void)m;s->state=GM6020_CLAMP_CONTACT;s->peak_current_raw=2500;return true;}
'''
end=r'''int main(void){
const uint16_t expected[5][4]={{1000,1000,2000,2000},{1000,1300,1700,2000},{1000,1300,2000,1000},{1000,1000,2000,2000},{1000,1300,1700,2000}};
for(scenario=0;scenario<3;++scenario){tick=starts=stops=entries=pwm_starts=0;clampExampleStarted=false;if(!setjmp(done))StartMainTask(NULL);
assert(entries==5&&starts==2&&stops==(scenario==1?4U:3U)&&pwm_starts==4);
for(unsigned j=0;j<5;++j)for(unsigned i=0;i<4;++i)assert(history[j][i]==expected[j][i]);
assert(bookDeviceState==BOOK_STATE_2&&bookStateActionAccepted);
assert(motorPos==42&&clampExampleFinished&&clampExampleTargetRPM==60);}
puts("PASS: state outputs, positive guarded clamp, stop states, repeat cycle, debounce, held button, rejected clamp, PWM initialization");}
'''
with tempfile.TemporaryDirectory() as temporary:
    w=Path(temporary)
    (w/'test.c').write_text(pre+typedefs+globals+helpers+body+end)
    subprocess.run(['gcc','-std=c11','-Wall','-Wextra','-Werror','-I'+str(p/'Tests/GM6020'),'-I'+str(p/'Core/Inc'),str(w/'test.c'),'-o',str(w/'test.exe')],check=True)
    subprocess.run([str(w/'test.exe')],check=True)
