from api.common.check_team_permission import check_file_team_permission
from api.db.services.file_service import FileService


def get_file_content(uid: str, file_id: str):
    """
    Authorize access and return file metadata for a separate storage read.

    :param uid: user ID
    :param file_id: file ID
    :return: (True, file_obj) or (False, error_message)
    """
    e, file = FileService.get_by_id(file_id)
    if not e:
        return False, "document not found"
    if not check_file_team_permission(file, uid):
        return False, "no authorization"
    return True, file
