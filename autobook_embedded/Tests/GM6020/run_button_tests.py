from pathlib import Path
import subprocess, tempfile
p=Path(__file__).resolve().parents[2]
s=(p/'Core/Src/main.c').read_text();a=s.index('void StartMainTask(void *argument)\n{');b=s.index('\n}\n',a)+3
body=s[a:b]
a=s.index('volatile int16_t clampExampleCurrentRaw');b=s.index('/* USER CODE END PV */',a);gl=s[a:b]
temporary=tempfile.TemporaryDirectory()
w=Path(temporary.name)
pre='''#include "gm6020.h"
#include <assert.h>
#include <setjmp.h>
#include <stdio.h>
#define GPIOA ((void*)3)
#define GPIO_PIN_0 1
#define GPIO_PIN_RESET 0
static unsigned tick, scenario, count;
static float targets[8];
static jmp_buf done;
uint32_t HAL_GetTick(void){return tick;}
void osDelay(unsigned delay){tick+=delay;if(tick>=700)longjmp(done,1);}
int HAL_GPIO_ReadPin(void *port,unsigned pin){(void)port;(void)pin;
if(scenario==2&&tick<60)return 0;
/* Bounce before first held press; two later release/press cycles. */
if((tick>=100&&tick<105)||(tick>=110&&tick<115)||(tick>=120&&tick<200)||
(tick>=260&&tick<340)||(tick>=400&&tick<470))return 0;
return 1;}
bool get6020Feedback(uint8_t b,uint8_t m,GM6020_Feedback *f){assert(b==1&&m==2);f->online=true;f->current_raw=0;return true;}
bool start6020Clamp(uint8_t b,uint8_t m,float rpm,const float pid[3],const GM6020_ClampConfig *c){
assert(b==1&&m==2);assert(pid[0]==40&&pid[1]==0.01f&&pid[2]==0);
assert(c->threshold_raw==2000&&c->confirm_ms==5&&c->max_run_ms==5000&&c->command_limit==10000);
assert(count<8);targets[count++]=rpm;
return !(scenario==1&&count==2);}
bool stop6020(uint8_t b,uint8_t m){(void)b;(void)m;return true;}
bool get6020ClampStatus(uint8_t b,uint8_t m,GM6020_ClampStatus *s){(void)b;(void)m;s->state=GM6020_CLAMP_CONTACT;s->peak_current_raw=2000;return true;}
'''
end='''int main(void){
for(scenario=0;scenario<3;++scenario){tick=0;count=0;if(!setjmp(done))StartMainTask(NULL);
assert(count==4);assert(targets[0]==-50);assert(targets[1]==50);
if(scenario==1){assert(targets[2]==50&&targets[3]==-50);}
else{assert(targets[2]==-50&&targets[3]==50);}
assert(clampExampleFinished);}
puts("PASS: actual MainTask direction toggle, debounce, held press, held-at-boot, rejected start, preserved parameters");}
'''
(w/'test.c').write_text(pre+gl+body+end)
subprocess.run(['gcc','-std=c11','-Wall','-Wextra','-Werror','-I'+str(p/'Tests/GM6020'),'-I'+str(p/'Core/Inc'),str(w/'test.c'),'-o',str(w/'test.exe')],check=True)
subprocess.run([str(w/'test.exe')],check=True)
