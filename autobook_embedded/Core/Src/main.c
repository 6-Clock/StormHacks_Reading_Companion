/* USER CODE BEGIN Header */
/**
  ******************************************************************************
  * @file           : main.c
  * @brief          : Main program body
  ******************************************************************************
  * @attention
  *
  * Copyright (c) 2026 STMicroelectronics.
  * All rights reserved.
  *
  * This software is licensed under terms that can be found in the LICENSE file
  * in the root directory of this software component.
  * If no LICENSE file comes with this software, it is provided AS-IS.
  *
  ******************************************************************************
  */
/* USER CODE END Header */
/* Includes ------------------------------------------------------------------*/
#include "main.h"
#include "cmsis_os.h"

/* Private includes ----------------------------------------------------------*/
/* USER CODE BEGIN Includes */
#include "gm6020.h"
#include "FreeRTOS.h"
#include "task.h"

/* USER CODE END Includes */

/* Private typedef -----------------------------------------------------------*/
/* USER CODE BEGIN PTD */
typedef enum {
  BOOK_STATE_1 = 1,
  BOOK_STATE_2 = 2,
  BOOK_STATE_3 = 3,
  BOOK_STATE_4 = 4,
  BOOK_STATE_5 = 5,
  BOOK_STATE_6 = 6,
  BOOK_STATE_7 = 7
} BookDeviceState;

typedef struct {
  uint32_t command;
  uint32_t requestId;
} BookUartCommand;

typedef struct {
  uint32_t requestId;
  const char *status;
  const char *reason;
} BookUartStatus;

/* USER CODE END PTD */

/* Private define ------------------------------------------------------------*/
/* USER CODE BEGIN PD */
#define BOOK_UART_RUN_COMMAND 50U
/* Independently tune each automatic transition delay (milliseconds). */
#define BOOK_STATE_2_TO_3_MS 1000U
#define BOOK_STATE_3_TO_4_MS 1000U
#define BOOK_STATE_4_TO_5_MS 1000U
#define BOOK_STATE_5_TO_6_MS 1000U
#define BOOK_UART_RX_BUFFER_SIZE 128U
#define BOOK_UART_STATUS_CAPACITY 16U

/* USER CODE END PD */

/* Private macro -------------------------------------------------------------*/
/* USER CODE BEGIN PM */

/* USER CODE END PM */

/* Private variables ---------------------------------------------------------*/
CAN_HandleTypeDef hcan1;

TIM_HandleTypeDef htim1;

UART_HandleTypeDef huart1;
DMA_HandleTypeDef hdma_usart1_rx;

/* Definitions for LibraryHandler */
osThreadId_t LibraryHandlerHandle;
const osThreadAttr_t LibraryHandler_attributes = {
  .name = "LibraryHandler",
  .stack_size = 512 * 4,
  .priority = (osPriority_t) osPriorityAboveNormal,
};
/* Definitions for MainTask */
osThreadId_t MainTaskHandle;
const osThreadAttr_t MainTask_attributes = {
  .name = "MainTask",
  .stack_size = 2048 * 4,
  .priority = (osPriority_t) osPriorityNormal,
};
/* Definitions for UartTast */
osThreadId_t UartTastHandle;
const osThreadAttr_t UartTast_attributes = {
  .name = "UartTast",
  .stack_size = 256 * 4,
  .priority = (osPriority_t) osPriorityLow,
};
/* USER CODE BEGIN PV */
volatile bool bookSequenceActive = false;
volatile bool bookSequenceStartRejected = false;
volatile uint32_t bookLastUartCommand = 0;
static uint32_t bookSequenceStateStartedMs;
static uint32_t bookSequenceRequestId;
static BookUartStatus bookUartStatusQueue[BOOK_UART_STATUS_CAPACITY];
static uint8_t bookUartStatusHead, bookUartStatusTail, bookUartStatusCount;
static bool bookUartTxNeedsResync;
static uint8_t bookUartRxByte;
static volatile uint8_t bookUartRxBuffer[BOOK_UART_RX_BUFFER_SIZE];
static volatile uint16_t bookUartRxHead, bookUartRxTail;
static volatile bool bookUartRxOverflow, bookUartRxNeedsRearm;
volatile uint32_t bookUartRxErrors = 0;
volatile BookDeviceState bookDeviceState = BOOK_STATE_1;
volatile bool bookStateActionAccepted = true;
/* Watch these in the debugger while tuning the clamp. Raw counts, not N*m. */
volatile int16_t clampExampleCurrentRaw = 0;
volatile uint16_t clampExamplePeakRaw = 0;
volatile GM6020_ClampState clampExampleState = GM6020_CLAMP_IDLE;
volatile bool clampExampleStarted = false;
volatile bool clampExampleStartRejected = false;
volatile bool clampExampleFinished = false;
volatile float clampExampleTargetRPM = 0.0f;


/* USER CODE END PV */

/* Private function prototypes -----------------------------------------------*/
void SystemClock_Config(void);
static void MX_GPIO_Init(void);
static void MX_DMA_Init(void);
static void MX_CAN1_Init(void);
static void MX_USART1_UART_Init(void);
static void MX_TIM1_Init(void);
void StartLibraryHandler(void *argument);
void StartMainTask(void *argument);
void StartUartTask(void *argument);

/* USER CODE BEGIN PFP */
static bool RunBookClamp(float closingRPM);
static void UpdateBookClampStatus(void);
static void EnterBookState(BookDeviceState state);
static bool ReadUartCommand(BookUartCommand *command);
static void ServiceBookSequence(uint32_t now);

/* USER CODE END PFP */

/* Private user code ---------------------------------------------------------*/
/* USER CODE BEGIN 0 */

/* USER CODE END 0 */

/**
  * @brief  The application entry point.
  * @retval int
  */
int main(void)
{

  /* USER CODE BEGIN 1 */

  /* USER CODE END 1 */

  /* MCU Configuration--------------------------------------------------------*/

  /* Reset of all peripherals, Initializes the Flash interface and the Systick. */
  HAL_Init();

  /* USER CODE BEGIN Init */

  /* USER CODE END Init */

  /* Configure the system clock */
  SystemClock_Config();

  /* USER CODE BEGIN SysInit */

  /* USER CODE END SysInit */

  /* Initialize all configured peripherals */
  MX_GPIO_Init();
  MX_DMA_Init();
  MX_CAN1_Init();
  MX_USART1_UART_Init();
  MX_TIM1_Init();
  /* USER CODE BEGIN 2 */
  if (GM6020_InitBus(1, &hcan1, GM6020_VOLTAGE) != HAL_OK)
  {
    Error_Handler();
  }

  /* One-byte interrupt reception stays active while UART TX runs. */
  if (HAL_UART_Receive_IT(&huart1, &bookUartRxByte, 1) != HAL_OK)
    Error_Handler();
  /* USER CODE END 2 */

  /* Init scheduler */
  osKernelInitialize();

  /* USER CODE BEGIN RTOS_MUTEX */
  /* add mutexes, ... */
  /* USER CODE END RTOS_MUTEX */

  /* USER CODE BEGIN RTOS_SEMAPHORES */
  /* add semaphores, ... */
  /* USER CODE END RTOS_SEMAPHORES */

  /* USER CODE BEGIN RTOS_TIMERS */
  /* start timers, add new ones, ... */
  /* USER CODE END RTOS_TIMERS */

  /* USER CODE BEGIN RTOS_QUEUES */
  /* add queues, ... */
  /* USER CODE END RTOS_QUEUES */

  /* Create the thread(s) */
  /* creation of LibraryHandler */
  LibraryHandlerHandle = osThreadNew(StartLibraryHandler, NULL, &LibraryHandler_attributes);

  /* creation of MainTask */
  MainTaskHandle = osThreadNew(StartMainTask, NULL, &MainTask_attributes);

  /* creation of UartTast */
  UartTastHandle = osThreadNew(StartUartTask, NULL, &UartTast_attributes);

  /* USER CODE BEGIN RTOS_THREADS */
  /* add threads, ... */
  /* USER CODE END RTOS_THREADS */

  /* USER CODE BEGIN RTOS_EVENTS */
  /* add events, ... */
  /* USER CODE END RTOS_EVENTS */

  /* Start scheduler */
  osKernelStart();

  /* We should never get here as control is now taken by the scheduler */

  /* Infinite loop */
  /* USER CODE BEGIN WHILE */
  while (1)
  {
    /* USER CODE END WHILE */

    /* USER CODE BEGIN 3 */
  }
  /* USER CODE END 3 */
}

/**
  * @brief System Clock Configuration
  * @retval None
  */
void SystemClock_Config(void)
{
  RCC_OscInitTypeDef RCC_OscInitStruct = {0};
  RCC_ClkInitTypeDef RCC_ClkInitStruct = {0};

  /** Configure the main internal regulator output voltage
  */
  __HAL_RCC_PWR_CLK_ENABLE();
  __HAL_PWR_VOLTAGESCALING_CONFIG(PWR_REGULATOR_VOLTAGE_SCALE1);

  /** Initializes the RCC Oscillators according to the specified parameters
  * in the RCC_OscInitTypeDef structure.
  */
  RCC_OscInitStruct.OscillatorType = RCC_OSCILLATORTYPE_HSE;
  RCC_OscInitStruct.HSEState = RCC_HSE_ON;
  RCC_OscInitStruct.PLL.PLLState = RCC_PLL_ON;
  RCC_OscInitStruct.PLL.PLLSource = RCC_PLLSOURCE_HSE;
  RCC_OscInitStruct.PLL.PLLM = 6;
  RCC_OscInitStruct.PLL.PLLN = 168;
  RCC_OscInitStruct.PLL.PLLP = RCC_PLLP_DIV2;
  RCC_OscInitStruct.PLL.PLLQ = 4;
  if (HAL_RCC_OscConfig(&RCC_OscInitStruct) != HAL_OK)
  {
    Error_Handler();
  }

  /** Initializes the CPU, AHB and APB buses clocks
  */
  RCC_ClkInitStruct.ClockType = RCC_CLOCKTYPE_HCLK|RCC_CLOCKTYPE_SYSCLK
                              |RCC_CLOCKTYPE_PCLK1|RCC_CLOCKTYPE_PCLK2;
  RCC_ClkInitStruct.SYSCLKSource = RCC_SYSCLKSOURCE_PLLCLK;
  RCC_ClkInitStruct.AHBCLKDivider = RCC_SYSCLK_DIV1;
  RCC_ClkInitStruct.APB1CLKDivider = RCC_HCLK_DIV4;
  RCC_ClkInitStruct.APB2CLKDivider = RCC_HCLK_DIV2;

  if (HAL_RCC_ClockConfig(&RCC_ClkInitStruct, FLASH_LATENCY_5) != HAL_OK)
  {
    Error_Handler();
  }
}

/**
  * @brief CAN1 Initialization Function
  * @param None
  * @retval None
  */
static void MX_CAN1_Init(void)
{

  /* USER CODE BEGIN CAN1_Init 0 */

  /* USER CODE END CAN1_Init 0 */

  /* USER CODE BEGIN CAN1_Init 1 */

  /* USER CODE END CAN1_Init 1 */
  hcan1.Instance = CAN1;
  hcan1.Init.Prescaler = 3;
  hcan1.Init.Mode = CAN_MODE_NORMAL;
  hcan1.Init.SyncJumpWidth = CAN_SJW_1TQ;
  hcan1.Init.TimeSeg1 = CAN_BS1_10TQ;
  hcan1.Init.TimeSeg2 = CAN_BS2_3TQ;
  hcan1.Init.TimeTriggeredMode = DISABLE;
  hcan1.Init.AutoBusOff = DISABLE;
  hcan1.Init.AutoWakeUp = DISABLE;
  hcan1.Init.AutoRetransmission = DISABLE;
  hcan1.Init.ReceiveFifoLocked = DISABLE;
  hcan1.Init.TransmitFifoPriority = DISABLE;
  if (HAL_CAN_Init(&hcan1) != HAL_OK)
  {
    Error_Handler();
  }
  /* USER CODE BEGIN CAN1_Init 2 */

  /* USER CODE END CAN1_Init 2 */

}

/**
  * @brief TIM1 Initialization Function
  * @param None
  * @retval None
  */
static void MX_TIM1_Init(void)
{

  /* USER CODE BEGIN TIM1_Init 0 */

  /* USER CODE END TIM1_Init 0 */

  TIM_MasterConfigTypeDef sMasterConfig = {0};
  TIM_OC_InitTypeDef sConfigOC = {0};
  TIM_BreakDeadTimeConfigTypeDef sBreakDeadTimeConfig = {0};

  /* USER CODE BEGIN TIM1_Init 1 */

  /* USER CODE END TIM1_Init 1 */
  htim1.Instance = TIM1;
  htim1.Init.Prescaler = 167;
  htim1.Init.CounterMode = TIM_COUNTERMODE_UP;
  htim1.Init.Period = 19999;
  htim1.Init.ClockDivision = TIM_CLOCKDIVISION_DIV1;
  htim1.Init.RepetitionCounter = 0;
  htim1.Init.AutoReloadPreload = TIM_AUTORELOAD_PRELOAD_ENABLE;
  if (HAL_TIM_PWM_Init(&htim1) != HAL_OK)
  {
    Error_Handler();
  }
  sMasterConfig.MasterOutputTrigger = TIM_TRGO_RESET;
  sMasterConfig.MasterSlaveMode = TIM_MASTERSLAVEMODE_DISABLE;
  if (HAL_TIMEx_MasterConfigSynchronization(&htim1, &sMasterConfig) != HAL_OK)
  {
    Error_Handler();
  }
  sConfigOC.OCMode = TIM_OCMODE_PWM1;
  sConfigOC.Pulse = 0;
  sConfigOC.OCPolarity = TIM_OCPOLARITY_HIGH;
  sConfigOC.OCNPolarity = TIM_OCNPOLARITY_HIGH;
  sConfigOC.OCFastMode = TIM_OCFAST_DISABLE;
  sConfigOC.OCIdleState = TIM_OCIDLESTATE_RESET;
  sConfigOC.OCNIdleState = TIM_OCNIDLESTATE_RESET;
  if (HAL_TIM_PWM_ConfigChannel(&htim1, &sConfigOC, TIM_CHANNEL_1) != HAL_OK)
  {
    Error_Handler();
  }
  if (HAL_TIM_PWM_ConfigChannel(&htim1, &sConfigOC, TIM_CHANNEL_2) != HAL_OK)
  {
    Error_Handler();
  }
  if (HAL_TIM_PWM_ConfigChannel(&htim1, &sConfigOC, TIM_CHANNEL_3) != HAL_OK)
  {
    Error_Handler();
  }
  if (HAL_TIM_PWM_ConfigChannel(&htim1, &sConfigOC, TIM_CHANNEL_4) != HAL_OK)
  {
    Error_Handler();
  }
  sBreakDeadTimeConfig.OffStateRunMode = TIM_OSSR_DISABLE;
  sBreakDeadTimeConfig.OffStateIDLEMode = TIM_OSSI_DISABLE;
  sBreakDeadTimeConfig.LockLevel = TIM_LOCKLEVEL_OFF;
  sBreakDeadTimeConfig.DeadTime = 0;
  sBreakDeadTimeConfig.BreakState = TIM_BREAK_DISABLE;
  sBreakDeadTimeConfig.BreakPolarity = TIM_BREAKPOLARITY_HIGH;
  sBreakDeadTimeConfig.AutomaticOutput = TIM_AUTOMATICOUTPUT_DISABLE;
  if (HAL_TIMEx_ConfigBreakDeadTime(&htim1, &sBreakDeadTimeConfig) != HAL_OK)
  {
    Error_Handler();
  }
  /* USER CODE BEGIN TIM1_Init 2 */

  /* USER CODE END TIM1_Init 2 */
  HAL_TIM_MspPostInit(&htim1);

}

/**
  * @brief USART1 Initialization Function
  * @param None
  * @retval None
  */
static void MX_USART1_UART_Init(void)
{

  /* USER CODE BEGIN USART1_Init 0 */

  /* USER CODE END USART1_Init 0 */

  /* USER CODE BEGIN USART1_Init 1 */

  /* USER CODE END USART1_Init 1 */
  huart1.Instance = USART1;
  huart1.Init.BaudRate = 115200;
  huart1.Init.WordLength = UART_WORDLENGTH_8B;
  huart1.Init.StopBits = UART_STOPBITS_1;
  huart1.Init.Parity = UART_PARITY_NONE;
  huart1.Init.Mode = UART_MODE_TX_RX;
  huart1.Init.HwFlowCtl = UART_HWCONTROL_NONE;
  huart1.Init.OverSampling = UART_OVERSAMPLING_16;
  if (HAL_UART_Init(&huart1) != HAL_OK)
  {
    Error_Handler();
  }
  /* USER CODE BEGIN USART1_Init 2 */

  /* USER CODE END USART1_Init 2 */

}

/**
  * Enable DMA controller clock
  */
static void MX_DMA_Init(void)
{

  /* DMA controller clock enable */
  __HAL_RCC_DMA2_CLK_ENABLE();

  /* DMA interrupt init */
  /* DMA2_Stream2_IRQn interrupt configuration */
  HAL_NVIC_SetPriority(DMA2_Stream2_IRQn, 5, 0);
  HAL_NVIC_EnableIRQ(DMA2_Stream2_IRQn);

}

/**
  * @brief GPIO Initialization Function
  * @param None
  * @retval None
  */
static void MX_GPIO_Init(void)
{
  GPIO_InitTypeDef GPIO_InitStruct = {0};
  /* USER CODE BEGIN MX_GPIO_Init_1 */

  /* USER CODE END MX_GPIO_Init_1 */

  /* GPIO Ports Clock Enable */
  __HAL_RCC_GPIOB_CLK_ENABLE();
  __HAL_RCC_GPIOD_CLK_ENABLE();
  __HAL_RCC_GPIOA_CLK_ENABLE();
  __HAL_RCC_GPIOH_CLK_ENABLE();
  __HAL_RCC_GPIOE_CLK_ENABLE();

  /*Configure GPIO pin : PA0 */
  GPIO_InitStruct.Pin = GPIO_PIN_0;
  GPIO_InitStruct.Mode = GPIO_MODE_INPUT;
  GPIO_InitStruct.Pull = GPIO_PULLUP;
  HAL_GPIO_Init(GPIOA, &GPIO_InitStruct);

  /* USER CODE BEGIN MX_GPIO_Init_2 */

  /* USER CODE END MX_GPIO_Init_2 */
}

/* USER CODE BEGIN 4 */
/* Book clamp helpers: no button handling or direction toggling here. */
static bool RunBookClamp(float closingRPM)
{
  const float pid[3] = {55.0f, 0.01f, 0.0f};
  const GM6020_ClampConfig config = {
    .threshold_raw = 2500,
    .confirm_ms = 5,
    .max_run_ms = 3000,   /* 3-second closure limit. */
    .command_limit = 10000
  };
  const bool started = start6020Clamp(1, 2, closingRPM, pid, &config);
  clampExampleStartRejected = !started;
  if (started)
  {
    clampExampleStarted = true;
    clampExampleFinished = false;
    clampExampleState = GM6020_CLAMP_RUNNING;
    clampExamplePeakRaw = 0;
    clampExampleTargetRPM = closingRPM;
  }
  else if (!clampExampleStarted)
  {
    (void)stop6020(1, 2);
    clampExampleState = GM6020_CLAMP_STOPPED;
    clampExampleFinished = true;
  }
  /* A rejected restart preserves the previous run/stop and its debug state. */
  return started;
}

static void UpdateBookClampStatus(void)
{
  GM6020_Feedback feedback;
  if (get6020Feedback(1, 2, &feedback) && feedback.online)
    clampExampleCurrentRaw = feedback.current_raw;
  if (clampExampleStarted)
  {
    GM6020_ClampStatus status;
    if (get6020ClampStatus(1, 2, &status))
    {
      clampExampleState = status.state;
      clampExamplePeakRaw = status.peak_current_raw;
      clampExampleFinished = status.state != GM6020_CLAMP_RUNNING;
    }
  }
}


/* Apply outputs once on entry. Button handling remains in MainTask.
 * State 2 starts a guarded +60 RPM run; contact/faults do not advance state. */
static void EnterBookState(BookDeviceState state)
{
  uint16_t pulse1, pulse2, pulse3, pulse4;
  switch (state)
  {
    case BOOK_STATE_2:
      pulse1 = 2100, pulse2 = 1600; pulse3 = 1400; pulse4 = 800; // side servos clamp down on book
      break;
    case BOOK_STATE_3:
      pulse1 = 1000, pulse2 = 1600; pulse3 = 1400; pulse4 = 800; // page gripping servo exposes a page
      break;
    case BOOK_STATE_4:
      pulse1 = 1000, pulse2 = 1600; pulse3 = 1400; pulse4 = 1800; // wiper servo flips the page
      break;
    case BOOK_STATE_5:
      pulse1 = 1000, pulse2 = 1600; pulse3 = 2100; pulse4 = 1800; //the outer page gripping servo opens up
      break;
    case BOOK_STATE_6:
      pulse1 = 1000, pulse2 = 1600; pulse3 = 1400; pulse4 = 1800; // ... then clamps down on the new page
      break;
    case BOOK_STATE_1:
    default:
      state = BOOK_STATE_1; // Initial position, all servos opened up
      pulse1 = 2100, pulse2 = 1200; pulse3 = 1800; pulse4 = 800;
      break;
  }

  if (state != BOOK_STATE_2)
  {
    bookStateActionAccepted = stop6020(1, 2);
    clampExampleStarted = false;
    clampExampleStartRejected = false;
    clampExampleFinished = true;
    clampExampleTargetRPM = 0.0f;
    clampExampleState = GM6020_CLAMP_STOPPED;
  }

  __HAL_TIM_SET_COMPARE(&htim1, TIM_CHANNEL_1, pulse1);
  __HAL_TIM_SET_COMPARE(&htim1, TIM_CHANNEL_2, pulse2);
  __HAL_TIM_SET_COMPARE(&htim1, TIM_CHANNEL_3, pulse3);
  __HAL_TIM_SET_COMPARE(&htim1, TIM_CHANNEL_4, pulse4);
  bookDeviceState = state;

  if (state == BOOK_STATE_2)
    bookStateActionAccepted = RunBookClamp(60.0f);
}

static bool CanReadBookCommand(void)
{
  taskENTER_CRITICAL();
  /* Keep a terminal reply slot available throughout an accepted sequence. */
  const bool available = bookUartStatusCount <= BOOK_UART_STATUS_CAPACITY - 2U;
  taskEXIT_CRITICAL();
  return available;
}

static void QueueBookStatus(const char *status, uint32_t requestId, const char *reason)
{
  const BookUartStatus message = {requestId, status, reason};
  taskENTER_CRITICAL();
  bookUartStatusQueue[bookUartStatusHead] = message;
  bookUartStatusHead = (uint8_t)((bookUartStatusHead + 1U) % BOOK_UART_STATUS_CAPACITY);
  ++bookUartStatusCount;
  taskEXIT_CRITICAL();
}

static void HandleBookCommand(const BookUartCommand *command)
{
  bookLastUartCommand = command->command;
  if (command->command != BOOK_UART_RUN_COMMAND) return;
  if (bookSequenceActive)
  {
    QueueBookStatus("BUSY", command->requestId, NULL);
    return;
  }
  EnterBookState(BOOK_STATE_2);
  bookSequenceStartRejected = !bookStateActionAccepted;
  bookSequenceActive = bookStateActionAccepted;
  bookSequenceStateStartedMs = HAL_GetTick();
  bookSequenceRequestId = command->requestId;
  QueueBookStatus(bookSequenceActive ? "ACK" : "ERROR", command->requestId,
                  bookSequenceActive ? NULL : "START_REJECTED");
}

static const char *BookClampFailure(void)
{
  GM6020_ClampStatus status;
  if (!get6020ClampStatus(1, 2, &status)) return "FEEDBACK_LOST";
  switch (status.state)
  {
    case GM6020_CLAMP_FEEDBACK_LOST: return "FEEDBACK_LOST";
    case GM6020_CLAMP_TIMEOUT: return "TIMEOUT";
    case GM6020_CLAMP_SERVICE_LATE: return "SERVICE_LATE";
    default: return NULL;
  }
}

static void AbortBookSequence(const char *reason)
{
  (void)stop6020(1, 2);
  bookSequenceActive = false;
  QueueBookStatus("ERROR", bookSequenceRequestId, reason);
}

/* Start/service the automatic sequence only from MainTask. */
static void ServiceBookSequence(uint32_t now)
{
  if (!bookSequenceActive) return;

  if (bookDeviceState == BOOK_STATE_2)
  {
    const char *reason = BookClampFailure();
    if (reason != NULL)
    {
      AbortBookSequence(reason);
      return;
    }
  }

  uint32_t delayMs;
  switch (bookDeviceState)
  {
    case BOOK_STATE_2: delayMs = BOOK_STATE_2_TO_3_MS; break;
    case BOOK_STATE_3: delayMs = BOOK_STATE_3_TO_4_MS; break;
    case BOOK_STATE_4: delayMs = BOOK_STATE_4_TO_5_MS; break;
    case BOOK_STATE_5: delayMs = BOOK_STATE_5_TO_6_MS; break;
    default: AbortBookSequence("INVALID_STATE"); return;
  }
  if ((uint32_t)(now - bookSequenceStateStartedMs) < delayMs) return;

  /* Timer-only transitions. State 3 stops drive before changing servo outputs. */
  if (bookDeviceState == BOOK_STATE_2)
  {
    /* Do not let the motor service set a fault between the check and stop. */
    taskENTER_CRITICAL();
    const char *reason = BookClampFailure();
    if (reason == NULL) EnterBookState(BOOK_STATE_3);
    taskEXIT_CRITICAL();
    if (reason != NULL) { AbortBookSequence(reason); return; }
  }
  else EnterBookState((BookDeviceState)(bookDeviceState + 1));
  bookSequenceStateStartedMs = HAL_GetTick();
  if (!bookStateActionAccepted) AbortBookSequence("STOP_REJECTED");
  else if (bookDeviceState == BOOK_STATE_6)
  {
    bookSequenceActive = false;
    QueueBookStatus("DONE", bookSequenceRequestId, NULL);
  }
}

/* UART command input: command and optional request ID, terminated by CR or LF.
 * Call from exactly one task. No blocking receive or heap allocation.
 * Invalid/overflowed lines are discarded in full, rather than partly parsed. */
static bool ReadUartCommand(BookUartCommand *command)
{
  static uint32_t parsed, parsedCommand;
  static uint8_t digits;
  static bool discardLine, readingId;
  if (command == NULL) return false;

  /* Recover interrupt reception after a HAL error without blocking a task. */
  taskENTER_CRITICAL();
  if (bookUartRxNeedsRearm &&
      HAL_UART_Receive_IT(&huart1, &bookUartRxByte, 1) == HAL_OK)
    bookUartRxNeedsRearm = false;
  taskEXIT_CRITICAL();

  for (unsigned n = 0; n < BOOK_UART_RX_BUFFER_SIZE; ++n)
  {
    uint8_t byte;
    taskENTER_CRITICAL();
    if (bookUartRxOverflow)
    {
      /* Flush queued fragments. Resume after the next received line ending. */
      bookUartRxTail = bookUartRxHead;
      bookUartRxOverflow = false;
      parsed = 0; parsedCommand = 0; digits = 0; readingId = false; discardLine = true;
    }
    if (bookUartRxTail == bookUartRxHead)
    {
      taskEXIT_CRITICAL();
      return false;
    }
    byte = bookUartRxBuffer[bookUartRxTail];
    bookUartRxTail = (uint16_t)((bookUartRxTail + 1U) % BOOK_UART_RX_BUFFER_SIZE);
    taskEXIT_CRITICAL();

    if (byte == '\r' || byte == '\n')
    {
      const bool accepted = digits != 0 && !discardLine;
      const BookUartCommand result = {readingId ? parsedCommand : parsed,
                                      readingId ? parsed : 0U};
      parsed = 0; parsedCommand = 0; digits = 0; discardLine = false; readingId = false;
      if (accepted) { *command = result; return true; }
    }
    else if (!discardLine)
    {
      if (byte == ' ' && digits != 0U && !readingId)
      {
        parsedCommand = parsed; parsed = 0; digits = 0; readingId = true;
      }
      else if (byte < '0' || byte > '9' || digits >= 10U ||
          parsed > (UINT32_MAX - (uint32_t)(byte - '0')) / 10U)
        discardLine = true;
      else { parsed = parsed * 10U + (uint32_t)(byte - '0'); ++digits; }
    }
  }
  return false;
}

void HAL_UART_RxCpltCallback(UART_HandleTypeDef *uart)
{
  if (uart != &huart1) return;
  const uint16_t next = (uint16_t)((bookUartRxHead + 1U) % BOOK_UART_RX_BUFFER_SIZE);
  if (next == bookUartRxTail) bookUartRxOverflow = true;
  else
  {
    bookUartRxBuffer[bookUartRxHead] = bookUartRxByte;
    bookUartRxHead = next;
  }
  bookUartRxNeedsRearm = HAL_UART_Receive_IT(uart, &bookUartRxByte, 1) != HAL_OK;
}

void HAL_UART_ErrorCallback(UART_HandleTypeDef *uart)
{
  if (uart != &huart1) return;
  ++bookUartRxErrors;
  bookUartRxOverflow = true; /* Drop a command potentially corrupted by error. */
  bookUartRxNeedsRearm = HAL_UART_Receive_IT(uart, &bookUartRxByte, 1) != HAL_OK;
}

void HAL_CAN_RxFifo0MsgPendingCallback(CAN_HandleTypeDef *can)
{
  CAN_RxHeaderTypeDef header;
  uint8_t data[8];
  while (HAL_CAN_GetRxFifoFillLevel(can, CAN_RX_FIFO0) != 0)
  {
    if (HAL_CAN_GetRxMessage(can, CAN_RX_FIFO0, &header, data) != HAL_OK) break;
    GM6020_OnRx(can, &header, data);
  }
}

uint16_t motorPos;

/* USER CODE END 4 */

/* USER CODE BEGIN Header_StartLibraryHandler */
/**
  * @brief  Function implementing the LibraryHandler thread.
  * @param  argument: Not used
  * @retval None
  */
/* USER CODE END Header_StartLibraryHandler */
void StartLibraryHandler(void *argument)
{
  /* USER CODE BEGIN 5 */
  TickType_t wake = xTaskGetTickCount();
  for (;;)
  {
    GM6020_Service();
    vTaskDelayUntil(&wake, pdMS_TO_TICKS(1));
  }
  /* USER CODE END 5 */
}

/* USER CODE BEGIN Header_StartMainTask */
/**
* @brief Function implementing the MainTask thread.
* @param argument: Not used
* @retval None
*/
/* USER CODE END Header_StartMainTask */
void StartMainTask(void *argument)
{
  /* USER CODE BEGIN StartMainTask */
  (void)argument;

  /* Load state 1 pulses before enabling the outputs; no clamp motion at boot. */
  EnterBookState(BOOK_STATE_1);
  if (HAL_TIM_PWM_Start(&htim1, TIM_CHANNEL_1) != HAL_OK ||
      HAL_TIM_PWM_Start(&htim1, TIM_CHANNEL_2) != HAL_OK ||
      HAL_TIM_PWM_Start(&htim1, TIM_CHANNEL_3) != HAL_OK ||
      HAL_TIM_PWM_Start(&htim1, TIM_CHANNEL_4) != HAL_OK)
    Error_Handler();

  /* PA0 pull-up: LOW is pressed. Ignore a button held during startup. */
  bool buttonSample = HAL_GPIO_ReadPin(GPIOA, GPIO_PIN_0) == GPIO_PIN_RESET;
  bool buttonStable = buttonSample;
  uint32_t buttonChangedMs = HAL_GetTick();

  for (;;)
  {
    const uint32_t now = HAL_GetTick();
    motorPos = get6020Pos(1, 2);
    BookUartCommand command;
    while (CanReadBookCommand() && ReadUartCommand(&command))
      HandleBookCommand(&command);
    ServiceBookSequence(HAL_GetTick());
    const bool pressed = HAL_GPIO_ReadPin(GPIOA, GPIO_PIN_0) == GPIO_PIN_RESET;
    if (pressed != buttonSample)
    {
      buttonSample = pressed;
      buttonChangedMs = now;
    }
    if (buttonSample != buttonStable && (uint32_t)(now - buttonChangedMs) >= 30U)
    {
      buttonStable = buttonSample;
      if (buttonStable && !bookSequenceActive)
      {
        /* Manual steps are ignored while the UART sequence runs. */
        const BookDeviceState next = bookDeviceState == BOOK_STATE_6 ?
            BOOK_STATE_2 : (BookDeviceState)(bookDeviceState + 1);
        EnterBookState(next);
      }
    }

    UpdateBookClampStatus();
    osDelay(5);
  }
  /* USER CODE END StartMainTask */
}

/* USER CODE BEGIN Header_StartUartTask */
/**
* @brief Function implementing the UartTast thread.
* @param argument: Not used
* @retval None
*/
/* USER CODE END Header_StartUartTask */
void StartUartTask(void *argument)
{
  /* USER CODE BEGIN StartUartTask */
  (void)argument;
  char uartBuff[64];
  /* Infinite loop */
  for(;;)
  {
    BookUartStatus message;
    taskENTER_CRITICAL();
    const bool hasStatus = bookUartStatusCount != 0U;
    if (hasStatus) message = bookUartStatusQueue[bookUartStatusTail];
    taskEXIT_CRITICAL();

    int length;
    if (hasStatus)
      length = snprintf(uartBuff, sizeof(uartBuff), "%s %lu%s%s\r\n",
                        message.status, (unsigned long)message.requestId,
                        message.reason != NULL ? " " : "",
                        message.reason != NULL ? message.reason : "");
    else if (motorPos == GM6020_INVALID_POSITION)
      length = snprintf(uartBuff, sizeof(uartBuff), "offline\r\n");
    else
      length = snprintf(uartBuff, sizeof(uartBuff), "%u\r\n", (unsigned)motorPos);

    if (length > 0 && (size_t)length < sizeof(uartBuff))
    {
      /* A failed blocking TX may leave a partial line on the host. */
      if (bookUartTxNeedsResync)
        bookUartTxNeedsResync = HAL_UART_Transmit(&huart1, (uint8_t *)"\r\n", 2, 10) != HAL_OK;
      if (!bookUartTxNeedsResync)
      {
        bookUartTxNeedsResync = HAL_UART_Transmit(&huart1, (uint8_t *)uartBuff,
                                                (uint16_t)length, 10) != HAL_OK;
        if (hasStatus && !bookUartTxNeedsResync)
        {
          taskENTER_CRITICAL();
          bookUartStatusTail = (uint8_t)((bookUartStatusTail + 1U) % BOOK_UART_STATUS_CAPACITY);
          --bookUartStatusCount;
          taskEXIT_CRITICAL();
        }
      }
    }
    osDelay(10);
  }
  /* USER CODE END StartUartTask */
}

/**
  * @brief  This function is executed in case of error occurrence.
  * @retval None
  */
void Error_Handler(void)
{
  /* USER CODE BEGIN Error_Handler_Debug */
  /* User can add his own implementation to report the HAL error return state */
  __disable_irq();
  while (1)
  {
  }
  /* USER CODE END Error_Handler_Debug */
}
#ifdef USE_FULL_ASSERT
/**
  * @brief  Reports the name of the source file and the source line number
  *         where the assert_param error has occurred.
  * @param  file: pointer to the source file name
  * @param  line: assert_param error line source number
  * @retval None
  */
void assert_failed(uint8_t *file, uint32_t line)
{
  /* USER CODE BEGIN 6 */
  /* User can add his own implementation to report the file name and line number,
     ex: printf("Wrong parameters value: file %s on line %d\r\n", file, line) */
  /* USER CODE END 6 */
}
#endif /* USE_FULL_ASSERT */
