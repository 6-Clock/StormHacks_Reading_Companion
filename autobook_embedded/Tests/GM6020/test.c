#include "gm6020.h"
#include <assert.h>
#include <math.h>
#include <string.h>
#include <stdio.h>
static uint32_t now, free_count=3, count;
static struct {uint32_t id; uint8_t data[8]; void *bus;} tx[8];
uint32_t HAL_GetTick(void){return now;}
HAL_StatusTypeDef HAL_CAN_ConfigFilter(CAN_HandleTypeDef*c,CAN_FilterTypeDef*f){(void)c;assert(f->SlaveStartFilterBank==14);return HAL_OK;}
HAL_StatusTypeDef HAL_CAN_Start(CAN_HandleTypeDef*c){(void)c;return HAL_OK;}
HAL_StatusTypeDef HAL_CAN_Stop(CAN_HandleTypeDef*c){(void)c;return HAL_OK;}
HAL_StatusTypeDef HAL_CAN_ActivateNotification(CAN_HandleTypeDef*c,uint32_t n){(void)c;(void)n;return HAL_OK;}
uint32_t HAL_CAN_GetTxMailboxesFreeLevel(CAN_HandleTypeDef*c){(void)c;return free_count;}
HAL_StatusTypeDef HAL_CAN_AddTxMessage(CAN_HandleTypeDef*c,CAN_TxHeaderTypeDef*h,uint8_t*d,uint32_t*m){assert(count<8);tx[count].id=h->StdId;tx[count].bus=c->Instance;memcpy(tx[count++].data,d,8);*m=0;return HAL_OK;}
static void rx(CAN_HandleTypeDef*c,unsigned id,int rpm){CAN_RxHeaderTypeDef h={0};h.StdId=0x204+id;h.DLC=8;uint16_t r=(uint16_t)rpm;uint8_t d[8]={0x1f,0xff,(uint8_t)(r>>8),(uint8_t)r,0xff,0xfe,42,0};GM6020_OnRx(c,&h,d);}
static void step(unsigned t){now=t;count=0;GM6020_Service();}
static void load(CAN_HandleTypeDef *c,unsigned id,int current){
CAN_RxHeaderTypeDef h={0};h.StdId=0x204+id;h.DLC=8;
uint16_t bits=(uint16_t)current;uint8_t d[8]={0,0,0,0,(uint8_t)(bits>>8),(uint8_t)bits,30,0};GM6020_OnRx(c,&h,d);
}
static void pose(CAN_HandleTypeDef *c,unsigned id,unsigned position,int rpm){
CAN_RxHeaderTypeDef h={0};h.StdId=0x204+id;h.DLC=8;uint16_t r=(uint16_t)rpm;
uint8_t d[8]={(uint8_t)(position>>8),(uint8_t)position,(uint8_t)(r>>8),(uint8_t)r,0,0,30,0};GM6020_OnRx(c,&h,d);
}
static int command(unsigned frame,unsigned slot){unsigned v=((unsigned)tx[frame].data[slot*2]<<8)|tx[frame].data[slot*2+1];return v>=32768?(int)v-65536:(int)v;}
int main(void){CAN_HandleTypeDef c1={CAN1},c2={CAN2};float pid[3]={100,0,0};GM6020_Feedback f;
assert(get6020Pos(1,1)==UINT16_MAX);assert(!set6020RPM(1,1,10,pid));
assert(GM6020_InitBus(1,&c1,GM6020_VOLTAGE)==HAL_OK);assert(GM6020_InitBus(2,&c2,GM6020_CURRENT)==HAL_OK);
assert(!set6020RPM(0,1,10,pid));assert(!set6020RPM(1,8,10,pid));assert(!set6020RPM(1,1,NAN,pid));assert(!set6020RPM(1,1,321,pid));
rx(&c1,1,-20);assert(get6020Feedback(1,1,&f)&&f.online&&f.rpm==-20&&f.current_raw==-2&&f.temperature_c==42);assert(get6020Pos(1,1)==8191);
assert(set6020RPM(1,1,100,pid));assert(set6020RPM(1,7,-320,pid));rx(&c1,7,0);assert(set6020RPM(2,5,320,pid));rx(&c2,5,0);
step(0);step(1);assert(count==4);assert(tx[0].id==0x1ff&&tx[1].id==0x2ff&&tx[2].id==0x1fe&&tx[3].id==0x2fe);
assert(tx[0].data[0]==0x2e&&tx[0].data[1]==0xe0); /* 12000 */
assert(tx[1].data[4]==0x9e&&tx[1].data[5]==0x58); /* -25000 */
assert(tx[1].data[6]==0&&tx[1].data[7]==0);assert(tx[3].data[0]==0x40&&tx[3].data[1]==0); /* 16384 */
assert(stop6020(1,1));step(2);assert(tx[0].data[0]==0&&tx[0].data[1]==0);
for(unsigned t=3;t<=100;++t){step(t);}assert(!get6020Feedback(1,7,&f)||!f.online);assert(get6020Pos(1,7)==UINT16_MAX);for(unsigned i=0;i<count;i++)for(unsigned j=0;j<8;j++)assert(tx[i].data[j]==0);
free_count=1;step(101);assert(count==0);free_count=3;rx(&c1,7,0);step(150);assert(tx[1].data[4]==0); /* late service zero */
/* Recovered feedback resumes persistent target on next timely iteration. */
step(151);assert(tx[1].data[4]==0x9e);
/* Integral anti-windup: 100 cycles saturated, then reduced error. */
float pi[3]={1000,1000,0};assert(set6020RPM(1,2,100,pi));for(unsigned t=152;t<252;t++){now=t;rx(&c1,2,0);step(t);}
now=252;rx(&c1,2,99);step(252);assert(tx[0].data[2]==3&&tx[0].data[3]==233); /* 1001, no stored windup */
/* Torque readout must be fresh and calibrated; raw sign is preserved. */
int16_t raw;float torque;
assert(get6020TorqueRaw(1,2,&raw)&&raw==-2);assert(!get6020Torque(1,2,&torque));
assert(!set6020TorqueCalibration(1,2,NAN,0));
assert(set6020TorqueCalibration(1,2,0.001f,-2));
assert(get6020Torque(1,2,&torque)&&torque==0);
now=253;load(&c1,2,-102);assert(get6020Torque(1,2,&torque)&&fabsf(torque+0.1f)<0.00001f);
GM6020_ClampConfig cfg={1000,3,50,200};GM6020_ClampStatus cs;
load(&c1,2,0);assert(start6020Clamp(1,2,10,pid,&cfg));
assert(!set6020RPM(1,2,60,pid));step(253);assert(tx[0].data[2]==0&&tx[0].data[3]==200);
now=254;load(&c1,2,1200);step(254);assert(get6020ClampStatus(1,2,&cs)&&cs.state==GM6020_CLAMP_RUNNING);
now=255;load(&c1,2,0);step(255); /* spike rejected, confirmation resets */
for(unsigned t=256;t<=259;++t){now=t;load(&c1,2,-1200);step(t);}
assert(get6020ClampStatus(1,2,&cs)&&cs.state==GM6020_CLAMP_CONTACT&&cs.peak_current_raw==1200);
assert(tx[0].data[2]==0&&tx[0].data[3]==0);assert(!set6020RPM(1,2,60,pid));
assert(stop6020(1,2));assert(!set6020RPM(1,2,60,pid));assert(!start6020Clamp(1,2,10,pid,&cfg));
now=260;load(&c1,2,0);assert(start6020Clamp(1,2,10,pid,&cfg));
for(unsigned t=260;t<=310;++t){now=t;load(&c1,2,0);step(t);}
assert(get6020ClampStatus(1,2,&cs)&&cs.state==GM6020_CLAMP_TIMEOUT);
now=311;load(&c1,2,0);cfg.max_run_ms=500;assert(start6020Clamp(1,2,10,pid,&cfg));
for(unsigned t=311;t<=411;++t){step(t);}
assert(get6020ClampStatus(1,2,&cs)&&cs.state==GM6020_CLAMP_FEEDBACK_LOST);
assert(!get6020TorqueRaw(1,2,&raw)&&!get6020Torque(1,2,&torque));
now=412;load(&c1,2,0);assert(start6020Clamp(1,2,10,pid,&cfg));step(412);step(433);
assert(get6020ClampStatus(1,2,&cs)&&cs.state==GM6020_CLAMP_SERVICE_LATE);
/* Full signed magnitude, including -32768; guard works on bus 2. */
now=434;load(&c2,5,0);cfg.confirm_ms=0;cfg.threshold_raw=32768;assert(start6020Clamp(2,5,-10,pid,&cfg));
load(&c2,5,-32768);step(434);assert(get6020ClampStatus(2,5,&cs)&&cs.state==GM6020_CLAMP_CONTACT&&cs.peak_current_raw==32768);
assert(tx[3].data[0]==0&&tx[3].data[1]==0);
/* No duplicate service can advance debounce; fresh frames are required. */
now=435;load(&c1,2,0);cfg.threshold_raw=1000;cfg.confirm_ms=3;assert(start6020Clamp(1,2,10,pid,&cfg));load(&c1,2,1500);
for(unsigned t=435;t<=440;++t){step(t);}assert(get6020ClampStatus(1,2,&cs)&&cs.state==GM6020_CLAMP_RUNNING);
assert(stop6020(1,2));assert(get6020ClampStatus(1,2,&cs)&&cs.state==GM6020_CLAMP_STOPPED);
/* Wraparound timestamps maintain continuous confirmation. */
now=UINT32_MAX-2;load(&c1,2,0);assert(start6020Clamp(1,2,10,pid,&cfg));
load(&c1,2,1500);now=UINT32_MAX;load(&c1,2,1500);now=0;load(&c1,2,1500);
assert(get6020ClampStatus(1,2,&cs)&&cs.state==GM6020_CLAMP_CONTACT);
/* Position control validation, shortest wrap path, speed cap and mode transitions. */
float pos_pid[3]={1,0,0},speed_pid[3]={10,0,0};
assert(!set6020Pos(1,3,100,pos_pid,speed_pid,50)); /* no feedback */
now=1000;pose(&c1,3,8190,0);step(1000);
assert(!set6020Pos(1,3,8192,pos_pid,speed_pid,50));
assert(!set6020Pos(1,3,100,NULL,speed_pid,50));
assert(!set6020Pos(1,3,100,pos_pid,speed_pid,NAN));
assert(!set6020Pos(1,3,100,pos_pid,speed_pid,0));
assert(!set6020Pos(1,3,100,pos_pid,speed_pid,321));
assert(!set6020Pos(1,2,100,pos_pid,speed_pid,50)); /* clamp latch */
assert(set6020Pos(1,3,20,pos_pid,speed_pid,50));step(1001);assert(command(0,2)==220);
now=1002;pose(&c1,3,2,0);assert(set6020Pos(1,3,8172,pos_pid,speed_pid,50));step(1002);assert(command(0,2)==-220);
now=1003;pose(&c1,3,0,0);assert(set6020Pos(1,3,4096,pos_pid,speed_pid,50));step(1003);assert(command(0,2)==500);
now=1004;pose(&c1,3,4096,0);assert(set6020Pos(1,3,0,pos_pid,speed_pid,50));step(1004);assert(command(0,2)==500); /* half-turn tie */
now=1005;pose(&c1,3,100,0);assert(set6020Pos(1,3,100,pos_pid,speed_pid,50));step(1005);assert(command(0,2)==0);
now=1006;pose(&c1,3,100,10);step(1006);assert(command(0,2)==-100); /* speed braking at target */
now=1007;pose(&c1,3,150,0);step(1007);assert(command(0,2)==-500); /* disturbance correction */
assert(stop6020(1,3));step(1008);assert(command(0,2)==0);
assert(set6020Pos(1,3,200,pos_pid,speed_pid,50));assert(set6020RPM(1,3,-10,speed_pid));step(1009);assert(command(0,2)==-100);
/* Identical repeated position commands preserve integral; stop and new mode reset it. */
float pos_pi[3]={0,100,0};now=1010;pose(&c1,3,0,0);
assert(set6020Pos(1,3,20,pos_pi,speed_pid,50));step(1010);assert(command(0,2)==20);
assert(set6020Pos(1,3,20,pos_pi,speed_pid,50));step(1011);assert(command(0,2)==40);
/* Outer anti-windup: prolonged large error then reduce to 10 counts. */
float pos_sat[3]={100,100,0};assert(set6020Pos(1,3,200,pos_sat,speed_pid,50));
for(unsigned t=1012;t<=1050;++t){now=t;pose(&c1,3,0,0);step(t);}
float pos_test[3]={1,100,0};assert(set6020Pos(1,3,200,pos_test,speed_pid,50));
for(unsigned t=1051;t<=1090;++t){now=t;pose(&c1,3,0,0);step(t);}
now=1091;pose(&c1,3,190,0);step(1091);assert(command(0,2)==110); /* 10 + 1 RPM integral, no windup */
for(unsigned t=1092;t<=1191;++t){step(t);}assert(command(0,2)==0); /* stale zero */
assert(!set6020Pos(1,3,200,pos_pid,speed_pid,50));
now=1192;pose(&c1,3,190,0);step(1192);assert(command(0,2)==110); /* recovery resets history */
/* Current-mode second bus also runs position controller. */
now=1193;pose(&c2,6,0,0);assert(set6020Pos(2,6,100,pos_pid,speed_pid,20));step(1193);assert(command(3,1)==200);
/* A guarded clamp replaces position control. */
GM6020_ClampConfig position_clamp={1000,3,500,1000};
assert(start6020Clamp(1,3,-10,speed_pid,&position_clamp));step(1194);assert(command(0,2)==-100);
puts("PASS: position shortest path, half-turn ties, speed cap, target braking, holding, mode switching, input checks, latch, repeated calls, anti-windup, stale feedback, bus 2");
puts("PASS: torque calibration, debounce, peak tracking, latching, command limit, explicit rearm, timeout, stale feedback, service gap, int16 minimum, fresh-frame debounce, tick wrap");
puts("PASS: protocol, signed feedback, bus isolation, validation, limits, stop, timeout, mailbox pressure, delayed service, anti-windup");}
