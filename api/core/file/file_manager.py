import base64
from collections.abc import Mapping

from configs import dify_config
from core.helper import ssrf_proxy
from core.model_runtime.entities import (
    AudioPromptMessageContent,
    DocumentPromptMessageContent,
    ImagePromptMessageContent,
    TextPromptMessageContent,
    VideoPromptMessageContent,
)
from core.model_runtime.entities.message_entities import PromptMessageContentUnionTypes
from core.tools.signature import sign_tool_file
from extensions.ext_storage import storage

from . import helpers
from .enums import FileAttribute
from .models import File, FileTransferMethod, FileType


def get_attr(*, file: File, attr: FileAttribute):
    match attr:
        case FileAttribute.TYPE:
            return file.type.value
        case FileAttribute.SIZE:
            return file.size
        case FileAttribute.NAME:
            return file.filename
        case FileAttribute.MIME_TYPE:
            return file.mime_type
        case FileAttribute.TRANSFER_METHOD:
            return file.transfer_method.value
        case FileAttribute.URL:
            return _to_url(file)
        case FileAttribute.EXTENSION:
            return file.extension
        case FileAttribute.RELATED_ID:
            return file.related_id


def to_prompt_message_content(
    f: File,
    /,
    *,
    image_detail_config: ImagePromptMessageContent.DETAIL | None = None,
) -> PromptMessageContentUnionTypes:
    """
    Convert a file to prompt message content.

    This function converts files to their appropriate prompt message content types.
    For supported file types (IMAGE, AUDIO, VIDEO, DOCUMENT), it creates the
    corresponding message content with proper encoding/URL.

    For unsupported file types, instead of raising an error, it returns a
    TextPromptMessageContent with a descriptive message about the file.

    Args:
        f: The file to convert
        image_detail_config: Optional detail configuration for image files

    Returns:
        PromptMessageContentUnionTypes: The appropriate message content type

    Raises:
        ValueError: If file extension or mime_type is missing
    """
    if f.extension is None:
        raise ValueError("Missing file extension")
    if f.mime_type is None:
        raise ValueError("Missing file mime_type")

    prompt_class_map: Mapping[FileType, type[PromptMessageContentUnionTypes]] = {
        FileType.IMAGE: ImagePromptMessageContent,
        FileType.AUDIO: AudioPromptMessageContent,
        FileType.VIDEO: VideoPromptMessageContent,
        FileType.DOCUMENT: DocumentPromptMessageContent,
    }

    # Check if file type is supported
    if f.type not in prompt_class_map:
        # For unsupported file types, return a text description
        return TextPromptMessageContent(data=f"[Unsupported file type: {f.filename} ({f.type.value})]")

    # Process supported file types
    params = {
        "base64_data": _get_encoded_string(f) if dify_config.MULTIMODAL_SEND_FORMAT == "base64" else "",
        "url": _to_url(f) if dify_config.MULTIMODAL_SEND_FORMAT == "url" else "",
        "format": f.extension.removeprefix("."),
        "mime_type": f.mime_type,
    }
    if f.type == FileType.IMAGE:
        params["detail"] = image_detail_config or ImagePromptMessageContent.DETAIL.LOW

    return prompt_class_map[f.type].model_validate(params)


def download(f: File, /):
    if f.transfer_method in (FileTransferMethod.TOOL_FILE, FileTransferMethod.LOCAL_FILE):
        return _download_file_content(f._storage_key)
    elif f.transfer_method == FileTransferMethod.REMOTE_URL:
        response = ssrf_proxy.get(f.remote_url, follow_redirects=True)
        response.raise_for_status()
        return response.content
    raise ValueError(f"unsupported transfer method: {f.transfer_method}")


def _download_file_content(path: str, /):
    """
    Download and return the contents of a file as bytes.

    This function loads the file from storage and ensures it's in bytes format.

    Args:
        path (str): The path to the file in storage.

    Returns:
        bytes: The contents of the file as a bytes object.

    Raises:
        ValueError: If the loaded file is not a bytes object.
    """
    data = storage.load(path, stream=False)
    if not isinstance(data, bytes):
        raise ValueError(f"file {path} is not a bytes object")
    return data


def _get_encoded_string(f: File, /):
    match f.transfer_method:
        case FileTransferMethod.REMOTE_URL:
            response = ssrf_proxy.get(f.remote_url, follow_redirects=True)
            response.raise_for_status()
            data = response.content
        case FileTransferMethod.LOCAL_FILE:
            data = _download_file_content(f._storage_key)
        case FileTransferMethod.TOOL_FILE:
            data = _download_file_content(f._storage_key)

    encoded_string = base64.b64encode(data).decode("utf-8")
    return encoded_string


def _to_url(f: File, /):
    if f.transfer_method == FileTransferMethod.REMOTE_URL:
        if f.remote_url is None:
            raise ValueError("Missing file remote_url")
        return f.remote_url
    elif f.transfer_method == FileTransferMethod.LOCAL_FILE:
        if f.related_id is None:
            raise ValueError("Missing file related_id")
        return f.remote_url or helpers.get_signed_file_url(upload_file_id=f.related_id)
    elif f.transfer_method == FileTransferMethod.TOOL_FILE:
        # add sign url
        if f.related_id is None or f.extension is None:
            raise ValueError("Missing file related_id or extension")
        return sign_tool_file(tool_file_id=f.related_id, extension=f.extension)
    else:
        raise ValueError(f"Unsupported transfer method: {f.transfer_method}")
