import logging
import os
import uuid
import environ
from django.conf import settings
from django.contrib.auth.decorators import login_required
from django.core.exceptions import FieldError, ValidationError
from django.core.files.uploadedfile import UploadedFile
from django.http import HttpRequest, HttpResponse, JsonResponse
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils.datastructures import MultiValueDictKeyError
from django.views.decorators.http import require_http_methods
from redbox_app.redbox_core.client import CoreApiClient
from redbox_app.redbox_core.models import (
    ChatHistory,
    ChatMessage,
    ChatRoleEnum,
    File,
    ProcessingStatusEnum,
    User,
)
from requests.exceptions import HTTPError
from yarl import URL
from langchain_core.prompts import ChatPromptTemplate
from langchain.chains.qa_with_sources import load_qa_with_sources_chain
from langchain_community.chat_models import ChatLiteLLM
from redbox.llm.prompts.chat import (
    CONDENSE_QUESTION_PROMPT,
    STUFF_DOCUMENT_PROMPT,
    WITH_SOURCES_PROMPT,
)
from langchain.chains.llm import LLMChain
from langchain_elasticsearch import ApproxRetrievalStrategy, ElasticsearchStore
from redbox.model_db import MODEL_PATH
from langchain_community.embeddings import SentenceTransformerEmbeddings
from redbox.models.chat import ChatMessage, ChatRequest, ChatResponse, SourceDocument

env = environ.Env()
CHATMODEL = env.str("CHATMODEL")
llm = ChatLiteLLM(
    model="gpt-3.5-turbo",
    streaming=True,
)


es = env.elasticsearch_client()
if env.elastic.subscription_level == "basic":
    strategy = ApproxRetrievalStrategy(hybrid=False)
elif env.elastic.subscription_level in ["platinum", "enterprise"]:
    strategy = ApproxRetrievalStrategy(hybrid=True)
else:
    raise ValueError(
        f"Unknown Elastic subscription level {env.elastic.subscription_level}"
    )
embedding_model = SentenceTransformerEmbeddings(
    model_name=env.embedding_model, cache_folder=MODEL_PATH
)


vector_store = ElasticsearchStore(
    es_connection=es,
    index_name="redbox-data-chunk",
    embedding=embedding_model,
    strategy=strategy,
    vector_query_field="embedding",
)

logger = logging.getLogger(__name__)
core_api = CoreApiClient(host=settings.CORE_API_HOST, port=settings.CORE_API_PORT)

CHUNK_SIZE = 1024
# move this somewhere
APPROVED_FILE_EXTENSIONS = [
    ".eml",
    ".html",
    ".json",
    ".md",
    ".msg",
    ".rst",
    ".rtf",
    ".txt",
    ".xml",
    ".csv",
    ".doc",
    ".docx",
    ".epub",
    ".epub",
    ".odt",
    ".pdf",
    ".ppt",
    ".pptx",
    ".tsv",
    ".xlsx",
    ".htm",
]
MAX_FILE_SIZE = 209715200  # 200 MB or 200 * 1024 * 1024


@require_http_methods(["GET"])
def homepage_view(request):
    return render(
        request,
        template_name="homepage.html",
        context={"request": request},
    )


@login_required
def documents_view(request):
    files = File.objects.filter(user=request.user).order_by("original_file")

    return render(
        request,
        template_name="documents.html",
        context={"request": request, "files": files},
    )


def get_file_extension(file):
    # TODO: use a third party checking service to validate this

    _, extension = os.path.splitext(file.name)
    return extension


@login_required
def upload_view(request):
    errors = []

    if request.method == "POST":
        # https://django-storages.readthedocs.io/en/1.13.2/backends/amazon-S3.html
        try:
            uploaded_file = request.FILES["uploadDoc"]

            file_extension = get_file_extension(uploaded_file)

            if uploaded_file.name is None:
                errors.append("File has no name")
            if uploaded_file.content_type is None:
                errors.append("File has no content-type")
            if uploaded_file.size > MAX_FILE_SIZE:
                errors.append("File is larger than 200MB")
            if file_extension not in APPROVED_FILE_EXTENSIONS:
                errors.append(f"File type {file_extension} not supported")
        except MultiValueDictKeyError:
            errors.append("No document selected")

        if not errors:
            errors += ingest_file(uploaded_file, request.user)

        if not errors:
            return redirect(reverse(documents_view))

    return render(
        request,
        template_name="upload.html",
        context={
            "request": request,
            "errors": {"upload_doc": errors},
            "uploaded": not errors,
        },
    )


def ingest_file(uploaded_file: UploadedFile, user: User) -> list[str]:
    errors: list[str] = []
    try:
        file = File.objects.create(
            processing_status=ProcessingStatusEnum.uploaded.value,
            user=user,
            original_file=uploaded_file,
            original_file_name=uploaded_file.name,
        )
        file.save()
    except (ValueError, FieldError, ValidationError) as e:
        logger.error(
            "Error creating File model object for %s.", uploaded_file, exc_info=e
        )
        errors.append(e.args[0])
    else:
        try:
            upload_file_response = core_api.upload_file(file.unique_name, user)
        except HTTPError as e:
            logger.error("Error uploading file object %s.", file, exc_info=e)
            file.delete()
            errors.append("failed to connect to core-api")
        else:
            file.core_file_uuid = upload_file_response.uuid
            file.save()
    return errors


@login_required
def remove_doc_view(request, doc_id: uuid):
    file = File.objects.get(pk=doc_id)
    errors: list[str] = []

    if request.method == "POST":
        try:
            core_api.delete_file(file.core_file_uuid, request.user)
        except HTTPError as e:
            logger.error("Error deleting file object %s.", file, exc_info=e)
            errors.append("There was an error deleting this file")

        else:
            logger.info("Removing document: %s", request.POST["doc_id"])
            file.delete()
            return redirect("documents")

    return render(
        request,
        template_name="remove-doc.html",
        context={
            "request": request,
            "doc_id": doc_id,
            "doc_name": file.name,
            "errors": errors,
        },
    )


@login_required
def sessions_view(request: HttpRequest, session_id: uuid = None):
    chat_history = ChatHistory.objects.filter(users=request.user).order_by(
        "-created_at"
    )

    messages = []
    if session_id:
        messages = ChatMessage.objects.filter(chat_history__id=session_id)
    endpoint = URL.build(scheme="ws", host=request.get_host(), path=r"/ws/chat/")
    context = {
        "session_id": session_id,
        "messages": messages,
        "chat_history": chat_history,
        "streaming": {"in_use": settings.USE_STREAMING, "endpoint": str(endpoint)},
    }

    return render(
        request,
        template_name="sessions.html",
        context=context,
    )


@require_http_methods(["POST"])
def post_message(request: HttpRequest) -> HttpResponse:
    message_text = request.POST.get("message", "New chat")

    # get current session, or create a new one
    if session_id := request.POST.get("session-id", None):
        session = ChatHistory.objects.get(id=session_id)
    else:
        session_name = message_text[0:20]
        session = ChatHistory(name=session_name, users=request.user)
        session.save()

    # save user message
    chat_message = ChatMessage(
        chat_history=session, text=message_text, role=ChatRoleEnum.user
    )
    chat_message.save()

    # get LLM response
    message_history = [
        {"role": message.role, "text": message.text}
        for message in ChatMessage.objects.all().filter(chat_history=session)
    ]
    if CHATMODEL == "6p6backend":
        question = message_history[-1].text
        previous_history = list(message_history[:-1])
        previous_history = ChatPromptTemplate.from_messages(
            (msg.role, msg.text) for msg in previous_history
        ).format_messages()

        docs_with_sources_chain = load_qa_with_sources_chain(
            llm,
            chain_type="stuff",
            prompt=WITH_SOURCES_PROMPT,
            document_prompt=STUFF_DOCUMENT_PROMPT,
            verbose=True,
        )

        condense_question_chain = LLMChain(llm=llm, prompt=CONDENSE_QUESTION_PROMPT)

        standalone_question = condense_question_chain(
            {"question": question, "chat_history": previous_history}
        )["text"]

        docs = vector_store.as_retriever(
            search_kwargs={
                "filter": {"term": {"creator_user_uuid.keyword": request.user}}
            }
        ).get_relevant_documents(standalone_question)

        result = docs_with_sources_chain(
            {
                "question": standalone_question,
                "input_documents": docs,
            },
        )

        source_documents = [
            SourceDocument(
                page_content=langchain_document.page_content,
                file_uuid=langchain_document.metadata.get("parent_doc_uuid"),
                page_numbers=langchain_document.metadata.get("page_numbers"),
            )
            for langchain_document in result.get("input_documents", [])
        ]
        return ChatResponse(
            output_text=result["output_text"], source_documents=source_documents
        )

        # output_text = "testing"
    else:
        response_data = core_api.rag_chat(message_history, request.user)
        output_text = response_data.output_text

    llm_message = ChatMessage(
        chat_history=session, text=output_text, role=ChatRoleEnum.ai
    )
    llm_message.save()
    if CHATMODEL == "6p6backend":
        files = "WPA_policy_document.pdf"
    else:
        doc_uuids: list[str] = [doc.file_uuid for doc in response_data.source_documents]
        files: list[File] = File.objects.filter(
            core_file_uuid__in=doc_uuids, user=request.user
        )
        llm_message.source_files.set(files)

    return redirect(reverse(sessions_view, args=(session.id,)))


@require_http_methods(["GET"])
@login_required
def file_status_api_view(request: HttpRequest) -> JsonResponse:
    file_id = request.GET.get("id", None)
    if not file_id:
        logger.error("Error getting file object information - no file ID provided %s.")
        return JsonResponse({"status": ProcessingStatusEnum.unknown.label})
    try:
        file = File.objects.get(pk=file_id)
    except File.DoesNotExist as ex:
        logger.error(
            "File object information not found in django - file does not exist %s.",
            file_id,
            exc_info=ex,
        )
        return JsonResponse({"status": ProcessingStatusEnum.unknown.label})
    try:
        core_file_status_response = core_api.get_file_status(
            file_id=file.core_file_uuid, user=request.user
        )
    except HTTPError as ex:
        logger.error(
            "File object information from core not found - file does not exist %s.",
            file_id,
            exc_info=ex,
        )
        if not file.processing_status:
            file.processing_status = ProcessingStatusEnum.unknown.label
            file.save()
        return JsonResponse({"status": file.processing_status})
    file.processing_status = core_file_status_response.processing_status
    file.save()
    return JsonResponse({"status": file.get_processing_status_text()})


@require_http_methods(["GET"])
def health(_request: HttpRequest) -> HttpResponse:
    """this required by ECS Fargate"""
    return HttpResponse(status=200)
