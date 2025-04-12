from typing import AsyncIterable, Union
from common.types import (
    SendTaskRequest,
    TaskSendParams,
    Message,
    TaskStatus,
    Artifact,
    TextPart,
    TaskState,
    SendTaskResponse,
    InternalError,
    JSONRPCResponse,
    SendTaskStreamingRequest,
    SendTaskStreamingResponse,
    TaskArtifactUpdateEvent,
    TaskStatusUpdateEvent,
    Task,
    TaskIdParams,
    PushNotificationConfig,
    SetTaskPushNotificationRequest,
    SetTaskPushNotificationResponse,
    TaskPushNotificationConfig,
    TaskNotFoundError,
    InvalidParamsError,
)
from common.server.task_manager import InMemoryTaskManager
from agents.langgraph.agent import NewsTweetAgent  # Updated from CurrencyAgent
from common.utils.push_notification_auth import PushNotificationSenderAuth
import common.server.utils as utils
import asyncio
import logging

logger = logging.getLogger(__name__)

class AgentTaskManager(InMemoryTaskManager):
    def __init__(self, agent: NewsTweetAgent, notification_sender_auth: PushNotificationSenderAuth):
        super().__init__()
        if not hasattr(agent, "invoke") or not hasattr(agent, "stream"):
            raise ValueError("Agent must support invoke and stream methods")
        self.agent = agent
        self.notification_sender_auth = notification_sender_auth

    async def _run_streaming_agent(self, request: SendTaskStreamingRequest):
        task_send_params: TaskSendParams = request.params
        query = self._get_user_query(task_send_params)

        try:
            async for item in self.agent.stream(query, task_send_params.sessionId):
                required_keys = {"is_task_complete", "require_user_input", "content"}
                if not all(key in item for key in required_keys):
                    raise ValueError(f"Invalid agent stream output: missing keys {required_keys}")

                is_task_complete = item["is_task_complete"]
                require_user_input = item["require_user_input"]
                artifact = None
                message = None
                parts = [{"type": "text", "text": item["content"]}]
                end_stream = False

                if not is_task_complete and not require_user_input:
                    task_state = TaskState.WORKING
                    message = Message(role="agent", parts=parts)
                elif require_user_input:
                    task_state = TaskState.INPUT_REQUIRED
                    message = Message(role="agent", parts=parts)
                    end_stream = True
                else:
                    task_state = TaskState.COMPLETED
                    artifact = Artifact(parts=parts, index=0, append=False)
                    end_stream = True

                task_status = TaskStatus(state=task_state, message=message)
                latest_task = await self.update_store(
                    task_send_params.id,
                    task_status,
                    None if artifact is None else [artifact],
                )
                await self.send_task_notification(latest_task)

                if artifact:
                    task_artifact_update_event = TaskArtifactUpdateEvent(
                        id=task_send_params.id, artifact=artifact
                    )
                    await self.enqueue_events_for_sse(
                        task_send_params.id, task_artifact_update_event
                    )

                task_update_event = TaskStatusUpdateEvent(
                    id=task_send_params.id, status=task_status, final=end_stream
                )
                await self.enqueue_events_for_sse(
                    task_send_params.id, task_update_event
                )

        except ValueError as e:
            logger.error(f"Agent stream error: {e}")
            await self.enqueue_events_for_sse(
                task_send_params.id,
                InternalError(message=f"Agent stream error: {str(e)}")
            )
        except Exception as e:
            logger.error(f"Unexpected error while streaming: {e}")
            await self.enqueue_events_for_sse(
                task_send_params.id,
                InternalError(message=f"Unexpected error: {str(e)}")
            )

    def _validate_request(
        self, request: Union[SendTaskRequest, SendTaskStreamingRequest]
    ) -> JSONRPCResponse | None:
        task_send_params: TaskSendParams = request.params
        if not task_send_params.message.parts:
            return JSONRPCResponse(
                id=request.id,
                error=InvalidParamsError(message="Message parts cannot be empty")
            )
        if not utils.are_modalities_compatible(
            task_send_params.acceptedOutputModes, NewsTweetAgent.SUPPORTED_CONTENT_TYPES
        ):
            logger.warning(
                "Unsupported output mode. Received %s, Support %s",
                task_send_params.acceptedOutputModes,
                NewsTweetAgent.SUPPORTED_CONTENT_TYPES,
            )
            return utils.new_incompatible_types_error(request.id)

        if task_send_params.pushNotification and not task_send_params.pushNotification.url:
            logger.warning("Push notification URL is missing")
            return JSONRPCResponse(
                id=request.id, error=InvalidParamsError(message="Push notification URL is missing")
            )

        return None

    async def on_send_task(self, request: SendTaskRequest) -> SendTaskResponse:
        """Handles the 'send task' request."""
        validation_error = self._validate_request(request)
        if validation_error:
            return SendTaskResponse(id=request.id, error=validation_error.error)

        if request.params.pushNotification:
            if not await self.set_push_notification_info(request.params.id, request.params.pushNotification):
                return SendTaskResponse(
                    id=request.id, error=InvalidParamsError(message="Push notification URL is invalid")
                )

        await self.upsert_task(request.params)
        task = await self.update_store(
            request.params.id, TaskStatus(state=TaskState.WORKING), None
        )
        await self.send_task_notification(task)

        task_send_params: TaskSendParams = request.params
        query = self._get_user_query(task_send_params)
        try:
            agent_response = self.agent.invoke(query, task_send_params.sessionId)
        except Exception as e:
            logger.error(f"Error invoking agent: {e}")
            return SendTaskResponse(
                id=request.id,
                error=InternalError(message=f"Error invoking agent: {str(e)}")
            )
        return await self._process_agent_response(request, agent_response)

    async def on_send_task_subscribe(
        self, request: SendTaskStreamingRequest
    ) -> AsyncIterable[SendTaskStreamingResponse] | JSONRPCResponse:
        try:
            error = self._validate_request(request)
            if error:
                return error

            await self.upsert_task(request.params)

            if request.params.pushNotification:
                if not await self.set_push_notification_info(request.params.id, request.params.pushNotification):
                    return JSONRPCResponse(
                        id=request.id, error=InvalidParamsError(message="Push notification URL is invalid")
                    )

            task_send_params: TaskSendParams = request.params
            sse_event_queue = await self.setup_sse_consumer(task_send_params.id, False)

            asyncio.create_task(self._run_streaming_agent(request))

            return self.dequeue_events_for_sse(
                request.id, task_send_params.id, sse_event_queue
            )
        except ValueError as e:
            logger.error(f"Invalid request parameters: {e}")
            return JSONRPCResponse(
                id=request.id,
                error=InvalidParamsError(message=str(e))
            )
        except Exception as e:
            logger.error(f"Error in SSE stream: {e}")
            return JSONRPCResponse(
                id=request.id,
                error=InternalError(message=f"Unexpected error: {str(e)}")
            )

    async def _process_agent_response(
        self, request: SendTaskRequest, agent_response: dict
    ) -> SendTaskResponse:
        """Processes the agent's response and updates the task store.

        Args:
            request: The task request containing params (id, historyLength, etc.).
            agent_response: Dictionary with 'content' and 'require_user_input' from agent.

        Returns:
            SendTaskResponse: Task result with updated status and history.
        """
        task_send_params: TaskSendParams = request.params
        task_id = task_send_params.id
        history_length = task_send_params.historyLength

        if not isinstance(agent_response, dict) or "content" not in agent_response or "require_user_input" not in agent_response:
            return SendTaskResponse(
                id=request.id,
                error=InternalError(message="Invalid agent response format")
            )

        parts = [{"type": "text", "text": agent_response["content"]}]
        artifact = None
        if agent_response["require_user_input"]:
            task_status = TaskStatus(
                state=TaskState.INPUT_REQUIRED,
                message=Message(role="agent", parts=parts),
            )
        else:
            task_status = TaskStatus(state=TaskState.COMPLETED)
            artifact = Artifact(parts=parts)
        task = await self.update_store(
            task_id, task_status, None if artifact is None else [artifact]
        )
        task_result = self.append_task_history(task, history_length)
        await self.send_task_notification(task)
        return SendTaskResponse(id=request.id, result=task_result)

    def _get_user_query(self, task_send_params: TaskSendParams) -> str:
        if not task_send_params.message.parts:
            raise ValueError("Message parts cannot be empty")
        part = task_send_params.message.parts[0]
        if not isinstance(part, TextPart):
            raise ValueError("Only text parts are supported")
        return part.text

    async def send_task_notification(self, task: Task):
        if not await self.has_push_notification_info(task.id):
            logger.debug(f"No push notification info found for task {task.id}")
            return
        push_info = await self.get_push_notification_info(task.id)

        logger.info(f"Notifying for task {task.id} => {task.status.state}")
        try:
            await self.notification_sender_auth.send_push_notification(
                push_info.url,
                data=task.model_dump(exclude_none=True)
            )
        except Exception as e:
            logger.error(f"Failed to send notification for task {task.id}: {e}")

    async def on_resubscribe_to_task(
        self, request: SendTaskStreamingRequest
    ) -> AsyncIterable[SendTaskStreamingResponse] | JSONRPCResponse:
        task_id_params: TaskIdParams = request.params
        try:
            sse_event_queue = await self.setup_sse_consumer(task_id_params.id, True)
            return self.dequeue_events_for_sse(request.id, task_id_params.id, sse_event_queue)
        except TaskNotFoundError:
            return JSONRPCResponse(
                id=request.id,
                error=TaskNotFoundError(message=f"Task {task_id_params.id} not found")
            )
        except Exception as e:
            logger.error(f"Error while reconnecting to SSE stream: {e}")
            return JSONRPCResponse(
                id=request.id,
                error=InternalError(message=f"Unexpected error: {str(e)}")
            )

    async def set_push_notification_info(self, task_id: str, push_notification_config: PushNotificationConfig):
        try:
            is_verified = await self.notification_sender_auth.verify_push_notification_url(push_notification_config.url)
            if not is_verified:
                return False
        except Exception as e:
            logger.error(f"Failed to verify push notification URL: {e}")
            return False

        await super().set_push_notification_info(task_id, push_notification_config)
        return True