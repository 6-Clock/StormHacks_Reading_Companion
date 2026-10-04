#include "gm6020.h"
#include "FreeRTOS.h"
#include "task.h"
#include <math.h>
#include <string.h>

typedef struct {
    GM6020_Feedback feedback;
    bool seen, enabled, derivative_ready;
    float target, gain[3], integral, previous_error;
    bool position_mode, position_derivative_ready;
    uint16_t position_target;
    float position_gain[3], position_integral, position_previous_error, max_rpm;
    float nm_per_raw;
    int16_t zero_raw;
    GM6020_ClampConfig clamp_config;
    GM6020_ClampState clamp_state;
    uint16_t peak_current_raw;
    bool above_threshold;
    uint32_t clamp_started_ms, above_since_ms, last_guard_sample_ms;
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
    m->position_integral = 0;
    m->position_previous_error = 0;
    m->position_derivative_ready = false;
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
    if (rpm != 0 && m->clamp_state != GM6020_CLAMP_IDLE) {
        taskEXIT_CRITICAL(); return false;
    }
    if (rpm == 0 && m->clamp_state == GM6020_CLAMP_RUNNING)
        m->clamp_state = GM6020_CLAMP_STOPPED;
    if (m->position_mode || m->target != rpm || memcmp(m->gain,pid,sizeof(m->gain)) != 0) reset(m);
    m->position_mode = false;
    m->target = rpm;
    memcpy(m->gain,pid,sizeof(m->gain));
    m->enabled = rpm != 0;
    if (!m->enabled) reset(m);
    taskEXIT_CRITICAL();
    return true;
}
bool set6020Pos(uint8_t bus, uint8_t id, uint16_t position,
                const float positionPID[3], const float rpmPID[3], float maxRPM)
{
    if (!valid(bus,id) || position > 8191 || !positionPID || !rpmPID ||
        !isfinite(maxRPM) || maxRPM <= 0 || maxRPM > 320) return false;
    for (unsigned i=0;i<3;++i)
        if (!isfinite(positionPID[i]) || positionPID[i] < 0 || positionPID[i] > 1000000.0f ||
            !isfinite(rpmPID[i]) || rpmPID[i] < 0 || rpmPID[i] > 1000000.0f) return false;
    taskENTER_CRITICAL();
    Motor *m = &buses[bus-1].motor[id-1];
    if (!buses[bus-1].can || m->clamp_state != GM6020_CLAMP_IDLE || !m->seen ||
        (uint32_t)(HAL_GetTick()-m->feedback.timestamp_ms) >= GM6020_FEEDBACK_TIMEOUT_MS) {
        taskEXIT_CRITICAL(); return false;
    }
    if (!m->position_mode || m->position_target != position || m->max_rpm != maxRPM ||
        memcmp(m->position_gain,positionPID,sizeof(m->position_gain)) != 0 ||
        memcmp(m->gain,rpmPID,sizeof(m->gain)) != 0) reset(m);
    m->position_target = position;
    m->max_rpm = maxRPM;
    memcpy(m->position_gain,positionPID,sizeof(m->position_gain));
    memcpy(m->gain,rpmPID,sizeof(m->gain));
    m->position_mode = true;
    m->enabled = true;
    taskEXIT_CRITICAL();
    return true;
}
/* Signed shortest error: (-4096,4096], including a positive half-turn tie. */
static float position_error(uint16_t target, uint16_t current)
{
    int32_t error = (int32_t)target-current;
    if (error > 4096) error -= 8192;
    if (error <= -4096) error += 8192;
    return (float)error;
}
static float position_speed(Motor *m, float dt)
{
    float error = position_error(m->position_target,m->feedback.position);
    if (fabsf(error) <= 8.0f) {
        m->position_integral = 0;
        m->position_derivative_ready = false;
        return 0;
    }
    float delta = error-m->position_previous_error;
    /* Avoid a derivative spike at the half-turn shortest-path discontinuity. */
    if (delta > 4096) delta -= 8192;
    if (delta < -4096) delta += 8192;
    float derivative = m->position_derivative_ready ? delta/dt : 0;
    float next_i = clamp(m->position_integral+m->position_gain[1]*error*dt,m->max_rpm);
    float pd = m->position_gain[0]*error+m->position_gain[2]*derivative;
    float raw = pd+next_i;
    if ((raw <= m->max_rpm && raw >= -m->max_rpm) ||
        (raw > m->max_rpm && error < 0) || (raw < -m->max_rpm && error > 0))
        m->position_integral = next_i;
    m->position_previous_error = error;
    m->position_derivative_ready = true;
    return clamp(pd+m->position_integral,m->max_rpm);
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
bool get6020TorqueRaw(uint8_t bus, uint8_t id, int16_t *out)
{
    GM6020_Feedback f;
    if (!out || !get6020Feedback(bus,id,&f) || !f.online) return false;
    *out = f.current_raw;
    return true;
}
bool set6020TorqueCalibration(uint8_t bus, uint8_t id, float scale, int16_t zero)
{
    if (!valid(bus,id) || !isfinite(scale) || scale <= 0 || scale > 1000) return false;
    taskENTER_CRITICAL();
    Motor *m = &buses[bus-1].motor[id-1];
    bool ok = buses[bus-1].can != NULL;
    if (ok) { m->nm_per_raw = scale; m->zero_raw = zero; }
    taskEXIT_CRITICAL();
    return ok;
}
bool get6020Torque(uint8_t bus, uint8_t id, float *out)
{
    if (!valid(bus,id) || !out) return false;
    taskENTER_CRITICAL();
    Motor *m = &buses[bus-1].motor[id-1];
    bool ok = m->nm_per_raw > 0 && m->seen &&
        (uint32_t)(HAL_GetTick()-m->feedback.timestamp_ms) < GM6020_FEEDBACK_TIMEOUT_MS;
    if (ok) *out = ((int32_t)m->feedback.current_raw-m->zero_raw)*m->nm_per_raw;
    taskEXIT_CRITICAL();
    return ok;
}
bool start6020Clamp(uint8_t bus, uint8_t id, float rpm, const float pid[3],
                    const GM6020_ClampConfig *config)
{
    if (!valid(bus,id) || !pid || !config || !isfinite(rpm) || rpm == 0 ||
        fabsf(rpm) > 320 || config->threshold_raw == 0 || config->threshold_raw > 32768U ||
        config->max_run_ms == 0 || config->max_run_ms > 0x7FFFFFFFU ||
        config->confirm_ms >= config->max_run_ms || config->command_limit == 0) return false;
    for (unsigned i=0;i<3;++i)
        if (!isfinite(pid[i]) || pid[i] < 0 || pid[i] > 1000000.0f) return false;
    taskENTER_CRITICAL();
    Bus *b = &buses[bus-1];
    Motor *m = &b->motor[id-1];
    uint32_t now = HAL_GetTick();
    uint16_t limit = b->mode == GM6020_VOLTAGE ? 25000U : 16384U;
    if (!b->can || config->command_limit > limit || !m->seen ||
        (uint32_t)(now-m->feedback.timestamp_ms) >= GM6020_FEEDBACK_TIMEOUT_MS) {
        taskEXIT_CRITICAL(); return false;
    }
    /* Do not begin against a load already above the threshold. */
    int32_t current = m->feedback.current_raw;
    uint16_t magnitude = (uint16_t)(current < 0 ? -current : current);
    if (magnitude >= config->threshold_raw) { taskEXIT_CRITICAL(); return false; }
    reset(m);
    m->position_mode = false;
    m->target = rpm;
    memcpy(m->gain,pid,sizeof(m->gain));
    m->clamp_config = *config;
    m->clamp_started_ms = now;
    m->last_guard_sample_ms = now;
    m->above_threshold = false;
    m->peak_current_raw = magnitude;
    m->clamp_state = GM6020_CLAMP_RUNNING;
    m->enabled = true;
    taskEXIT_CRITICAL();
    return true;
}
bool get6020ClampStatus(uint8_t bus, uint8_t id, GM6020_ClampStatus *out)
{
    if (!valid(bus,id) || !out) return false;
    taskENTER_CRITICAL();
    Motor *m = &buses[bus-1].motor[id-1];
    bool ok = buses[bus-1].can != NULL;
    if (ok) { out->state = m->clamp_state; out->peak_current_raw = m->peak_current_raw; }
    taskEXIT_CRITICAL();
    return ok;
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
        if (m->clamp_state == GM6020_CLAMP_RUNNING) {
            int32_t current = m->feedback.current_raw;
            uint16_t magnitude = (uint16_t)(current < 0 ? -current : current);
            uint32_t now = m->feedback.timestamp_ms;
            if (magnitude > m->peak_current_raw) m->peak_current_raw = magnitude;
            if ((uint32_t)(now-m->last_guard_sample_ms) > 20U) m->above_threshold = false;
            m->last_guard_sample_ms = now;
            if (magnitude >= m->clamp_config.threshold_raw) {
                if (!m->above_threshold) { m->above_threshold = true; m->above_since_ms = now; }
                if ((uint32_t)(now-m->above_since_ms) >= m->clamp_config.confirm_ms) {
                    m->clamp_state = GM6020_CLAMP_CONTACT;
                    m->enabled = false;
                }
            } else m->above_threshold = false;
        }
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
            if (m->clamp_state == GM6020_CLAMP_RUNNING) {
                if (!m->seen || (uint32_t)(now-m->feedback.timestamp_ms) >= GM6020_FEEDBACK_TIMEOUT_MS)
                    m->clamp_state = GM6020_CLAMP_FEEDBACK_LOST;
                else if ((uint32_t)(now-m->clamp_started_ms) >= m->clamp_config.max_run_ms)
                    m->clamp_state = GM6020_CLAMP_TIMEOUT;
                else if (!timely && elapsed > 20U)
                    m->clamp_state = GM6020_CLAMP_SERVICE_LATE;
                if (m->clamp_state != GM6020_CLAMP_RUNNING) m->enabled = false;
            }
            float motor_limit = m->clamp_state == GM6020_CLAMP_RUNNING ?
                m->clamp_config.command_limit : limit;
            if (timely && m->enabled && m->seen &&
                (uint32_t)(now-m->feedback.timestamp_ms) < GM6020_FEEDBACK_TIMEOUT_MS) {
                float target_rpm = m->position_mode ? position_speed(m,dt) : m->target;
                float error = target_rpm-m->feedback.rpm;
                float derivative = m->derivative_ready ? (error-m->previous_error)/dt : 0;
                float next_i = clamp(m->integral+m->gain[1]*error*dt,motor_limit);
                float pd = m->gain[0]*error+m->gain[2]*derivative;
                float raw = pd+next_i;
                /* Integrate only while unsaturated or driving back out of saturation. */
                if ((raw <= motor_limit && raw >= -motor_limit) || (raw > motor_limit && error < 0) ||
                    (raw < -motor_limit && error > 0)) m->integral = next_i;
                command = (int16_t)clamp(pd+m->integral,motor_limit);
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
