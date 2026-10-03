#include "gm6020.h"
#include "FreeRTOS.h"
#include "task.h"
#include <math.h>
#include <string.h>

typedef struct {
    GM6020_Feedback feedback;
    bool seen, enabled, derivative_ready;
    float target, gain[3], integral, previous_error;
} Motor;
typedef struct {
    CAN_HandleTypeDef *can;
    GM6020_Mode mode;
    Motor motor[7];
    uint32_t previous_ms;
    bool clock_ready;
} Bus;
static Bus buses[2];
static bool valid(uint8_t bus, uint8_t id)
{
    return bus >= 1 && bus <= 2 && id >= 1 && id <= 7;
}
static float clamp(float value, float limit)
{
    return value > limit ? limit : (value < -limit ? -limit : value);
}
static void reset(Motor *m)
{
    m->integral = 0;
    m->previous_error = 0;
    m->derivative_ready = false;
}
HAL_StatusTypeDef GM6020_InitBus(uint8_t bus, CAN_HandleTypeDef *can, GM6020_Mode mode)
{
    if (bus < 1 || bus > 2 || !can ||
        (mode != GM6020_VOLTAGE && mode != GM6020_CURRENT) ||
        (bus == 1 ? can->Instance != CAN1 : can->Instance != CAN2) ||
        buses[bus-1].can) return HAL_ERROR;
    CAN_FilterTypeDef f = {0};
    f.FilterBank = bus == 1 ? 0 : 14;
    f.FilterMode = CAN_FILTERMODE_IDMASK;
    f.FilterScale = CAN_FILTERSCALE_32BIT;
    f.FilterIdHigh = 0x200U << 5;
    f.FilterMaskIdHigh = 0x7F0U << 5;
    f.FilterMaskIdLow = 6; /* standard data frames only: IDE and RTR */
    f.FilterFIFOAssignment = CAN_RX_FIFO0;
    f.FilterActivation = ENABLE;
    f.SlaveStartFilterBank = 14;
    HAL_StatusTypeDef status = HAL_CAN_ConfigFilter(can, &f);
    if (status != HAL_OK) return status;
    Bus *b = &buses[bus-1];
    memset(b, 0, sizeof(*b));
    b->mode = mode;
    b->can = can; /* publish before notifications */
    status = HAL_CAN_Start(can);
    if (status == HAL_OK)
        status = HAL_CAN_ActivateNotification(can, CAN_IT_RX_FIFO0_MSG_PENDING);
    if (status != HAL_OK) {
        (void)HAL_CAN_Stop(can);
        b->can = NULL;
    }
    return status;
}
bool set6020RPM(uint8_t bus, uint8_t id, float rpm, const float pid[3])
{
    if (!valid(bus,id) || !pid || !isfinite(rpm) || fabsf(rpm) > 320.0f)
        return false;
    for (unsigned i=0; i<3; ++i)
        if (!isfinite(pid[i]) || pid[i] < 0 || pid[i] > 1000000.0f) return false;
    taskENTER_CRITICAL();
    Bus *b = &buses[bus-1];
    if (!b->can) { taskEXIT_CRITICAL(); return false; }
    Motor *m = &b->motor[id-1];
    if (m->target != rpm || memcmp(m->gain,pid,sizeof(m->gain)) != 0) reset(m);
    m->target = rpm;
    memcpy(m->gain,pid,sizeof(m->gain));
    m->enabled = rpm != 0;
    if (!m->enabled) reset(m);
    taskEXIT_CRITICAL();
    return true;
}
bool stop6020(uint8_t bus, uint8_t id)
{
    const float zero[3] = {0};
    return set6020RPM(bus,id,0,zero);
}
bool get6020Feedback(uint8_t bus, uint8_t id, GM6020_Feedback *out)
{
    if (!valid(bus,id) || !out) return false;
    taskENTER_CRITICAL();
    Motor *m = &buses[bus-1].motor[id-1];
    bool seen = m->seen;
    *out = m->feedback;
    out->online = seen && (uint32_t)(HAL_GetTick()-out->timestamp_ms) < GM6020_FEEDBACK_TIMEOUT_MS;
    taskEXIT_CRITICAL();
    return seen;
}
uint16_t get6020Pos(uint8_t bus, uint8_t id)
{
    GM6020_Feedback f;
    return get6020Feedback(bus,id,&f) && f.online ? f.position : GM6020_INVALID_POSITION;
}
static uint16_t u16(const uint8_t *data)
{
    return (uint16_t)(((uint16_t)data[0] << 8) | data[1]);
}
static int16_t s16(const uint8_t *data)
{
    uint16_t v = u16(data);
    return (int16_t)(v < 32768U ? (int32_t)v : (int32_t)v-65536);
}
void GM6020_OnRx(CAN_HandleTypeDef *can, const CAN_RxHeaderTypeDef *h, const uint8_t data[8])
{
    if (!can || !h || !data || h->IDE != CAN_ID_STD || h->RTR != CAN_RTR_DATA ||
        h->DLC != 8 || h->StdId < 0x205 || h->StdId > 0x20B || u16(data) > 8191) return;
    for (unsigned i=0;i<2;++i) if (buses[i].can == can) {
        Motor *m = &buses[i].motor[h->StdId-0x205];
        m->feedback.position = u16(data);
        m->feedback.rpm = s16(data+2);
        m->feedback.current_raw = s16(data+4);
        m->feedback.temperature_c = data[6];
        m->feedback.timestamp_ms = HAL_GetTick();
        m->seen = true;
        return;
    }
}
void GM6020_Service(void)
{
    taskENTER_CRITICAL(); /* RX0 IRQ priority must be >= FreeRTOS syscall priority. */
    uint32_t now = HAL_GetTick();
    for (unsigned bi=0;bi<2;++bi) {
        Bus *b = &buses[bi];
        if (!b->can) continue;
        uint32_t elapsed = now-b->previous_ms;
        if (b->clock_ready && elapsed == 0) continue;
        bool timely = b->clock_ready && elapsed <= 20;
        b->previous_ms = now;
        b->clock_ready = true;
        float dt = elapsed * 0.001f;
        float limit = b->mode == GM6020_VOLTAGE ? 25000.0f : 16384.0f;
        uint8_t frames[2][8] = {{0}};
        for (unsigned mi=0;mi<7;++mi) {
            Motor *m = &b->motor[mi];
            int16_t command = 0;
            if (timely && m->enabled && m->seen &&
                (uint32_t)(now-m->feedback.timestamp_ms) < GM6020_FEEDBACK_TIMEOUT_MS) {
                float error = m->target-m->feedback.rpm;
                float derivative = m->derivative_ready ? (error-m->previous_error)/dt : 0;
                float next_i = clamp(m->integral+m->gain[1]*error*dt,limit);
                float pd = m->gain[0]*error+m->gain[2]*derivative;
                float raw = pd+next_i;
                /* Integrate only while unsaturated or driving back out of saturation. */
                if ((raw <= limit && raw >= -limit) || (raw > limit && error < 0) ||
                    (raw < -limit && error > 0)) m->integral = next_i;
                command = (int16_t)clamp(pd+m->integral,limit);
                m->previous_error = error;
                m->derivative_ready = true;
            } else reset(m);
            uint16_t bits = (uint16_t)command;
            frames[mi/4][(mi%4)*2] = (uint8_t)(bits >> 8);
            frames[mi/4][(mi%4)*2+1] = (uint8_t)bits;
        }
        /* All-or-none mailbox availability prevents starving IDs 5..7. No waits. */
        if (HAL_CAN_GetTxMailboxesFreeLevel(b->can) >= 2) {
            CAN_TxHeaderTypeDef h = {0};
            h.IDE = CAN_ID_STD; h.RTR = CAN_RTR_DATA; h.DLC = 8;
            uint32_t mailbox;
            for (unsigned group=0;group<2;++group) {
                h.StdId = (b->mode == GM6020_VOLTAGE ? 0x1FFU : 0x1FEU) + group*0x100U;
                if (HAL_CAN_AddTxMessage(b->can,&h,frames[group],&mailbox) != HAL_OK) break;
            }
        }
    }
    taskEXIT_CRITICAL();
}
