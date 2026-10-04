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
#include <stdio.h>

/* Private includes ----------------------------------------------------------*/
/* USER CODE BEGIN Includes */
#include "gm6020.h"
#include "FreeRTOS.h"
#include "task.h"

/* USER CODE END Includes */

/* Private typedef -----------------------------------------------------------*/
/* USER CODE BEGIN PTD */

/* USER CODE END PTD */

/* Private define ------------------------------------------------------------*/
/* USER CODE BEGIN PD */

/* USER CODE END PD */

/* Private macro -------------------------------------------------------------*/
/* USER CODE BEGIN PM */

/* USER CODE END PM */

/* Private variables ---------------------------------------------------------*/
CAN_HandleTypeDef hcan1;

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
  .stack_size = 128 * 4,
  .priority = (osPriority_t) osPriorityLow,
};
/* USER CODE BEGIN PV */
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
void StartLibraryHandler(void *argument);
void StartMainTask(void *argument);
void StartUartTask(void *argument);

/* USER CODE BEGIN PFP */
static bool WaitForBookClampFeedback(uint32_t timeoutMs);
static bool RunBookClamp(float closingRPM);
static void UpdateBookClampStatus(void);

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
  /* USER CODE BEGIN 2 */
  if (GM6020_InitBus(1, &hcan1, GM6020_VOLTAGE) != HAL_OK)
  {
    Error_Handler();
  }

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
static bool WaitForBookClampFeedback(uint32_t timeoutMs)
{
  GM6020_Feedback feedback;
  const uint32_t started = HAL_GetTick();
  while ((uint32_t)(HAL_GetTick() - started) < timeoutMs)
  {
    if (get6020Feedback(1, 2, &feedback) && feedback.online)
    {
      clampExampleCurrentRaw = feedback.current_raw;
      return true;
    }
    osDelay(5);
  }
  (void)stop6020(1, 2);
  clampExampleState = GM6020_CLAMP_FEEDBACK_LOST;
  clampExampleFinished = true;
  return false;
}

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
  float nextRPM = -60.0f;

  /* Start once on boot. Direction changes only after an accepted run. */
  if (WaitForBookClampFeedback(2000U) && RunBookClamp(nextRPM))
    nextRPM = -nextRPM;

  /* PA0 pull-up: LOW is pressed. Ignore a button held during startup. */
  bool buttonSample = HAL_GPIO_ReadPin(GPIOA, GPIO_PIN_0) == GPIO_PIN_RESET;
  bool buttonStable = buttonSample;
  uint32_t buttonChangedMs = HAL_GetTick();

  for (;;)
  {
    const uint32_t now = HAL_GetTick();
    motorPos = get6020Pos(1, 2);
    const bool pressed = HAL_GPIO_ReadPin(GPIOA, GPIO_PIN_0) == GPIO_PIN_RESET;
    if (pressed != buttonSample)
    {
      buttonSample = pressed;
      buttonChangedMs = now;
    }
    if (buttonSample != buttonStable && (uint32_t)(now - buttonChangedMs) >= 30U)
    {
      buttonStable = buttonSample;
      /* One rearm per press; rejected starts leave the next direction intact. */
      if (buttonStable && RunBookClamp(nextRPM))
        nextRPM = -nextRPM;
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
  char uartBuff[16];
  /* Infinite loop */
  for(;;)
  {
	int length;
	if (motorPos == GM6020_INVALID_POSITION)
		length = snprintf(uartBuff, sizeof(uartBuff), "offline\r\n");
	else
		length = snprintf(uartBuff, sizeof(uartBuff),
						  "%u\r\n", (unsigned)motorPos);

    if (length > 0 && (size_t)length < sizeof(uartBuff)) {
    	HAL_UART_Transmit(&huart1, (uint8_t *)uartBuff, (uint16_t)length, 10);
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
