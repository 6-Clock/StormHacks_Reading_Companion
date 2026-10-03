#include <stdint.h>
#define CAN1 ((void*)1)
#define CAN2 ((void*)2)
#define CAN_ID_STD 0
#define CAN_RTR_DATA 0
#define CAN_FILTERMODE_IDMASK 0
#define CAN_FILTERSCALE_32BIT 0
#define CAN_RX_FIFO0 0
#define CAN_IT_RX_FIFO0_MSG_PENDING 1
#define ENABLE 1
typedef enum {HAL_OK, HAL_ERROR} HAL_StatusTypeDef;
typedef struct {void *Instance;} CAN_HandleTypeDef;
typedef struct {uint32_t FilterBank,FilterMode,FilterScale,FilterIdHigh,FilterIdLow,FilterMaskIdHigh,FilterMaskIdLow,FilterFIFOAssignment,FilterActivation,SlaveStartFilterBank;} CAN_FilterTypeDef;
typedef struct {uint32_t StdId,ExtId,IDE,RTR,DLC,TransmitGlobalTime;} CAN_TxHeaderTypeDef;
typedef CAN_TxHeaderTypeDef CAN_RxHeaderTypeDef;
uint32_t HAL_GetTick(void);
HAL_StatusTypeDef HAL_CAN_ConfigFilter(CAN_HandleTypeDef*,CAN_FilterTypeDef*);
HAL_StatusTypeDef HAL_CAN_Start(CAN_HandleTypeDef*);
HAL_StatusTypeDef HAL_CAN_Stop(CAN_HandleTypeDef*);
HAL_StatusTypeDef HAL_CAN_ActivateNotification(CAN_HandleTypeDef*,uint32_t);
uint32_t HAL_CAN_GetTxMailboxesFreeLevel(CAN_HandleTypeDef*);
HAL_StatusTypeDef HAL_CAN_AddTxMessage(CAN_HandleTypeDef*,CAN_TxHeaderTypeDef*,uint8_t*,uint32_t*);
