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
puts("PASS: protocol, signed feedback, bus isolation, validation, limits, stop, timeout, mailbox pressure, delayed service, anti-windup");}
