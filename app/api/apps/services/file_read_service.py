from api.common.check_team_permission import check_file_team_permission
from api.db.services.file_service import FileService


def get_file_content(uid: str, file_id: str):
    """
    Get file content and metadata for download.

    :param uid: user ID
    :param file_id: file ID
    :return: (success, (blob, file_obj)) or (success, error_message)
    """
    e, file = FileService.get_by_id(file_id)
    if not e:
        return False, "document not found"
    if not check_file_team_permission(file, uid):
        return False, "no authorization"
    return True, file
