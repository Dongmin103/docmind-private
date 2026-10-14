#
#  Copyright 2024 The InfiniFlow Authors. All Rights Reserved.
#
#  Licensed under the Apache License, Version 2.0 (the "License");
#  you may not use this file except in compliance with the License.
#  You may obtain a copy of the License at
#
#      http://www.apache.org/licenses/LICENSE-2.0
#
#  Unless required by applicable law or agreed to in writing, software
#  distributed under the License is distributed on an "AS IS" BASIS,
#  WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
#  See the License for the specific language governing permissions and
#  limitations under the License.
#
import inspect
import logging
import operator
import os
import sys
import time
import typing
from datetime import datetime, timezone
from enum import Enum
from functools import wraps

from quart_auth import AuthUser
from itsdangerous.url_safe import URLSafeTimedSerializer as Serializer
from peewee import (
    fn,
    InterfaceError,
    OperationalError,
    ProgrammingError,
    BigIntegerField,
    BooleanField,
    CharField,
    CompositeKey,
    DateTimeField,
    Field,
    FloatField,
    IntegerField,
    Metadata,
    Model,
    TextField,
)
from playhouse.migrate import MySQLMigrator, migrate
from playhouse.pool import PooledMySQLDatabase

from api import utils
from api.db import SerializedType
from api.utils.json_encode import json_dumps, json_loads
from api.utils.configs import deserialize_b64, serialize_b64

from common.time_utils import current_timestamp, timestamp_to_date, date_string_to_timestamp
from common.decorator import singleton
from common.constants import ParserType, MAXIMUM_TASK_PAGE_NUMBER
from common import settings


CONTINUOUS_FIELD_TYPE = {IntegerField, FloatField, DateTimeField}
AUTO_DATE_TIMESTAMP_FIELD_PREFIX = {"create", "start", "end", "update", "read_access", "write_access"}


class LongTextField(TextField):
    field_type = "LONGTEXT"


class JSONField(LongTextField):
    default_value = {}

    def __init__(self, object_hook=None, object_pairs_hook=None, **kwargs):
        self._object_hook = object_hook
        self._object_pairs_hook = object_pairs_hook
        super().__init__(**kwargs)

    def db_value(self, value):
        if value is None:
            value = self.default_value
        return json_dumps(value)

    def python_value(self, value):
        if not value:
            return self.default_value
        return json_loads(value, object_hook=self._object_hook, object_pairs_hook=self._object_pairs_hook)


class ListField(JSONField):
    default_value = []


class SerializedField(LongTextField):
    def __init__(self, serialized_type=SerializedType.PICKLE, object_hook=None, object_pairs_hook=None, **kwargs):
        self._serialized_type = serialized_type
        self._object_hook = object_hook
        self._object_pairs_hook = object_pairs_hook
        super().__init__(**kwargs)

    def db_value(self, value):
        if self._serialized_type == SerializedType.PICKLE:
            return serialize_b64(value, to_str=True)
        elif self._serialized_type == SerializedType.JSON:
            if value is None:
                return None
            return json_dumps(value, with_type=True)
        else:
            raise ValueError(f"the serialized type {self._serialized_type} is not supported")

    def python_value(self, value):
        if self._serialized_type == SerializedType.PICKLE:
            return deserialize_b64(value)
        elif self._serialized_type == SerializedType.JSON:
            if value is None:
                return {}
            return json_loads(value, object_hook=self._object_hook, object_pairs_hook=self._object_pairs_hook)
        else:
            raise ValueError(f"the serialized type {self._serialized_type} is not supported")


def is_continuous_field(cls: typing.Type) -> bool:
    if cls in CONTINUOUS_FIELD_TYPE:
        return True
    for p in cls.__bases__:
        if p in CONTINUOUS_FIELD_TYPE:
            return True
        elif p is not Field and p is not object:
            if is_continuous_field(p):
                return True
    else:
        return False


def auto_date_timestamp_field():
    return {f"{f}_time" for f in AUTO_DATE_TIMESTAMP_FIELD_PREFIX}


def auto_date_timestamp_db_field():
    return {f"f_{f}_time" for f in AUTO_DATE_TIMESTAMP_FIELD_PREFIX}


def remove_field_name_prefix(field_name):
    return field_name[2:] if field_name.startswith("f_") else field_name


class BaseModel(Model):
    create_time = BigIntegerField(null=True, index=True)
    create_date = DateTimeField(null=True, index=True)
    update_time = BigIntegerField(null=True, index=True)
    update_date = DateTimeField(null=True, index=True)

    def to_json(self):
        # This function is obsolete
        return self.to_dict()

    def to_dict(self):
        return self.__dict__["__data__"]

    def to_human_model_dict(self, only_primary_with: list = None):
        model_dict = self.__dict__["__data__"]

        if not only_primary_with:
            return {remove_field_name_prefix(k): v for k, v in model_dict.items()}

        human_model_dict = {}
        for k in self._meta.primary_key.field_names:
            human_model_dict[remove_field_name_prefix(k)] = model_dict[k]
        for k in only_primary_with:
            human_model_dict[k] = model_dict[f"f_{k}"]
        return human_model_dict

    @property
    def meta(self) -> Metadata:
        return self._meta

    @classmethod
    def get_primary_keys_name(cls):
        return cls._meta.primary_key.field_names if isinstance(cls._meta.primary_key, CompositeKey) else [cls._meta.primary_key.name]

    @classmethod
    def getter_by(cls, attr):
        return operator.attrgetter(attr)(cls)

    @classmethod
    def query(cls, reverse=None, order_by=None, **kwargs):
        filters = []
        for f_n, f_v in kwargs.items():
            attr_name = "%s" % f_n
            if not hasattr(cls, attr_name) or f_v is None:
                continue
            if type(f_v) in {list, set}:
                f_v = list(f_v)
                if is_continuous_field(type(getattr(cls, attr_name))):
                    if len(f_v) == 2:
                        for i, v in enumerate(f_v):
                            if isinstance(v, str) and f_n in auto_date_timestamp_field():
                                # time type: %Y-%m-%d %H:%M:%S
                                f_v[i] = date_string_to_timestamp(v)
                        lt_value = f_v[0]
                        gt_value = f_v[1]
                        if lt_value is not None and gt_value is not None:
                            filters.append(cls.getter_by(attr_name).between(lt_value, gt_value))
                        elif lt_value is not None:
                            filters.append(operator.attrgetter(attr_name)(cls) >= lt_value)
                        elif gt_value is not None:
                            filters.append(operator.attrgetter(attr_name)(cls) <= gt_value)
                else:
                    filters.append(operator.attrgetter(attr_name)(cls) << f_v)
            else:
                filters.append(operator.attrgetter(attr_name)(cls) == f_v)
        if filters:
            query_records = cls.select().where(*filters)
            if reverse is not None:
                if not order_by or not hasattr(cls, f"{order_by}"):
                    order_by = "create_time"
                if reverse is True:
                    query_records = query_records.order_by(cls.getter_by(f"{order_by}").desc())
                elif reverse is False:
                    query_records = query_records.order_by(cls.getter_by(f"{order_by}").asc())
            return [query_record for query_record in query_records]
        else:
            return []

    @classmethod
    def insert(cls, __data=None, **insert):
        if isinstance(__data, dict) and __data:
            __data[cls._meta.combined["create_time"]] = current_timestamp()
        if insert:
            insert["create_time"] = current_timestamp()

        return super().insert(__data, **insert)

    # update and insert will call this method
    @classmethod
    def _normalize_data(cls, data, kwargs):
        normalized = super()._normalize_data(data, kwargs)
        if not normalized:
            return {}

        normalized[cls._meta.combined["update_time"]] = current_timestamp()

        for f_n in AUTO_DATE_TIMESTAMP_FIELD_PREFIX:
            if {f"{f_n}_time", f"{f_n}_date"}.issubset(cls._meta.combined.keys()) and cls._meta.combined[f"{f_n}_time"] in normalized and normalized[cls._meta.combined[f"{f_n}_time"]] is not None:
                normalized[cls._meta.combined[f"{f_n}_date"]] = timestamp_to_date(normalized[cls._meta.combined[f"{f_n}_time"]])

        return normalized


class JsonSerializedField(SerializedField):
    def __init__(self, object_hook=utils.from_dict_hook, object_pairs_hook=None, **kwargs):
        super(JsonSerializedField, self).__init__(serialized_type=SerializedType.JSON, object_hook=object_hook, object_pairs_hook=object_pairs_hook, **kwargs)


class RetryingPooledMySQLDatabase(PooledMySQLDatabase):
    def __init__(self, *args, **kwargs):
        self.max_retries = kwargs.pop("max_retries", 5)
        self.retry_delay = kwargs.pop("retry_delay", 1)
        super().__init__(*args, **kwargs)

    def execute_sql(self, sql, params=None, commit=True):
        for attempt in range(self.max_retries + 1):
            try:
                return super().execute_sql(sql, params, commit)
            except (OperationalError, InterfaceError) as e:
                error_codes = [2013, 2006]
                error_messages = ["", "Lost connection"]
                should_retry = (hasattr(e, "args") and e.args and e.args[0] in error_codes) or (str(e) in error_messages) or (hasattr(e, "__class__") and e.__class__.__name__ == "InterfaceError")

                if should_retry and attempt < self.max_retries:
                    logging.warning(f"Database connection issue (attempt {attempt + 1}/{self.max_retries}): {e}")
                    self._handle_connection_loss()
                    time.sleep(self.retry_delay * (2**attempt))
                else:
                    logging.error(f"DB execution failure: {e}")
                    raise
        return None

    def _handle_connection_loss(self):
        # self.close_all()
        # self.connect()
        try:
            self.close()
        except Exception:
            pass
        try:
            self.connect()
        except Exception as e:
            logging.error(f"Failed to reconnect: {e}")
            time.sleep(0.1)
            try:
                self.connect()
            except Exception as e2:
                logging.error(f"Failed to reconnect on second attempt: {e2}")
                raise

    def begin(self):
        for attempt in range(self.max_retries + 1):
            try:
                return super().begin()
            except (OperationalError, InterfaceError) as e:
                error_codes = [2013, 2006]
                error_messages = ["", "Lost connection"]

                should_retry = (hasattr(e, "args") and e.args and e.args[0] in error_codes) or (str(e) in error_messages) or (hasattr(e, "__class__") and e.__class__.__name__ == "InterfaceError")

                if should_retry and attempt < self.max_retries:
                    logging.warning(f"Lost connection during transaction (attempt {attempt + 1}/{self.max_retries})")
                    self._handle_connection_loss()
                    time.sleep(self.retry_delay * (2**attempt))
                else:
                    raise
        return None


class PooledDatabase(Enum):
    MYSQL = RetryingPooledMySQLDatabase


class DatabaseMigrator(Enum):
    MYSQL = MySQLMigrator


@singleton
class BaseDataBase:
    def __init__(self):
        database_config = settings.DATABASE.copy()
        db_name = database_config.pop("name")

        pool_config = {
            "max_retries": 5,
            "retry_delay": 1,
        }
        database_config.update(pool_config)
        self.database_connection = PooledDatabase[settings.DATABASE_TYPE.upper()].value(db_name, **database_config)
        # self.database_connection = PooledDatabase[settings.DATABASE_TYPE.upper()].value(db_name, **database_config)
        logging.info("init database on cluster mode successfully")


def with_retry(max_retries=3, retry_delay=1.0):
    """Decorator: Add retry mechanism to database operations

    Args:
        max_retries (int): maximum number of retries
        retry_delay (float): initial retry delay (seconds), will increase exponentially

    Returns:
        decorated function
    """

    def decorator(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            last_exception = None
            for retry in range(max_retries):
                try:
                    return func(*args, **kwargs)
                except Exception as e:
                    last_exception = e
                    # get self and method name for logging
                    self_obj = args[0] if args else None
                    func_name = func.__name__
                    lock_name = getattr(self_obj, "lock_name", "unknown") if self_obj else "unknown"

                    if retry < max_retries - 1:
                        current_delay = retry_delay * (2**retry)
                        logging.warning(f"{func_name} {lock_name} failed: {str(e)}, retrying ({retry + 1}/{max_retries})")
                        time.sleep(current_delay)
                    else:
                        logging.error(f"{func_name} {lock_name} failed after all attempts: {str(e)}")

            if last_exception:
                raise last_exception
            return False

        return wrapper

    return decorator


class MysqlDatabaseLock:
    # Finite wait used for negative advisory-lock timeouts.
    BLOCKING_TIMEOUT = 60

    def __init__(self, lock_name, timeout=10, db=None):
        self.lock_name = lock_name
        timeout = int(timeout)
        if timeout < 0:
            logging.debug("Normalizing negative advisory-lock timeout to %d seconds", self.BLOCKING_TIMEOUT)
            self.timeout = self.BLOCKING_TIMEOUT
        else:
            self.timeout = timeout
        self.db = db if db else DB

    @with_retry(max_retries=3, retry_delay=1.0)
    def lock(self):
        # SQL parameters only support %s format placeholders
        cursor = self.db.execute_sql("SELECT GET_LOCK(%s, %s)", (self.lock_name, self.timeout))
        ret = cursor.fetchone()
        if ret[0] == 0:
            raise Exception(f"acquire mysql lock {self.lock_name} timeout")
        elif ret[0] == 1:
            return True
        else:
            raise Exception(f"failed to acquire lock {self.lock_name}")

    @with_retry(max_retries=3, retry_delay=1.0)
    def unlock(self):
        cursor = self.db.execute_sql("SELECT RELEASE_LOCK(%s)", (self.lock_name,))
        ret = cursor.fetchone()
        if ret[0] == 0:
            raise Exception(f"mysql lock {self.lock_name} was not established by this thread")
        elif ret[0] == 1:
            return True
        else:
            raise Exception(f"mysql lock {self.lock_name} does not exist")

    def __enter__(self):
        if isinstance(self.db, PooledMySQLDatabase):
            self.lock()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        if isinstance(self.db, PooledMySQLDatabase):
            self.unlock()

    def __call__(self, func):
        @wraps(func)
        def magic(*args, **kwargs):
            with self:
                return func(*args, **kwargs)

        return magic


class DatabaseLock(Enum):
    MYSQL = MysqlDatabaseLock


DB = BaseDataBase().database_connection
DB.lock = DatabaseLock[settings.DATABASE_TYPE.upper()].value


def close_connection():
    try:
        if DB:
            DB.close_stale(age=30)
    except Exception as e:
        logging.exception(e)


class DataBaseModel(BaseModel):
    class Meta:
        database = DB


@DB.connection_context()
@DB.lock("init_database_tables", 60)
def init_database_tables(alter_fields=[]):
    members = inspect.getmembers(sys.modules[__name__], inspect.isclass)
    table_objs = []
    create_failed_list = []
    for name, obj in members:
        if obj != DataBaseModel and issubclass(obj, DataBaseModel):
            table_objs.append(obj)

            if not obj.table_exists():
                logging.debug(f"start create table {obj.__name__}")
                try:
                    obj.create_table(safe=True)
                    logging.debug(f"create table success: {obj.__name__}")
                except Exception as e:
                    logging.exception(e)
                    create_failed_list.append(obj.__name__)
            else:
                logging.debug(f"table {obj.__name__} already exists, skip creation.")

    if create_failed_list:
        logging.error(f"create tables failed: {create_failed_list}")
        raise Exception(f"create tables failed: {create_failed_list}")
    migrate_db()


def fill_db_model_object(model_object, human_model_dict):
    for k, v in human_model_dict.items():
        attr_name = "%s" % k
        if hasattr(model_object.__class__, attr_name):
            setattr(model_object, attr_name, v)
    return model_object


class User(DataBaseModel, AuthUser):
    SENSITIVE_FIELDS = {"password", "access_token", "email"}

    id = CharField(max_length=32, primary_key=True)
    access_token = CharField(max_length=255, null=True, index=True)
    nickname = CharField(max_length=100, null=False, help_text="nicky name", index=True)
    password = CharField(max_length=255, null=True, help_text="password", index=True)
    email = CharField(max_length=255, null=False, help_text="email", unique=True)
    avatar = TextField(null=True, help_text="avatar base64 string")
    language = CharField(max_length=32, null=True, help_text="English|Chinese", default="Chinese" if "zh_CN" in os.getenv("LANG", "") else "English", index=True)
    color_schema = CharField(max_length=32, null=True, help_text="Bright|Dark", default="Bright", index=True)
    timezone = CharField(max_length=64, null=True, help_text="Timezone", default="UTC+8\tAsia/Shanghai", index=True)
    last_login_time = DateTimeField(null=True, index=True)
    is_authenticated = CharField(max_length=1, null=False, default="1", index=True)
    is_active = CharField(max_length=1, null=False, default="1", index=True)
    is_anonymous = CharField(max_length=1, null=False, default="0", index=True)
    login_channel = CharField(null=True, help_text="from which user login", index=True)
    status = CharField(max_length=1, null=True, help_text="is it validate(0: wasted, 1: validate)", default="1", index=True)
    is_superuser = BooleanField(null=True, help_text="is root", default=False, index=True)

    def __str__(self):
        return self.email

    def get_id(self):
        jwt = Serializer(secret_key=settings.get_secret_key())
        return jwt.dumps(str(self.access_token))

    def to_safe_dict(self, *, for_self: bool = False):
        """Return a dict with sensitive fields stripped for API responses.

        Email is treated as sensitive in generic serialization. Pass for_self=True
        when returning the authenticated user's own record (login, profile, etc.).
        """
        result = {k: v for k, v in self.to_dict().items() if k not in self.SENSITIVE_FIELDS}
        if for_self:
            result["email"] = self.email
        logging.debug("User %s serialized safely, filtered fields: %s", self.id, self.SENSITIVE_FIELDS)
        return result

    class Meta:
        db_table = "user"


class Tenant(DataBaseModel):
    id = CharField(max_length=32, primary_key=True)
    name = CharField(max_length=100, null=True, help_text="Tenant name", index=True)
    public_key = CharField(max_length=255, null=True, index=True)
    # These default model IDs historically use "" for an unconfigured value.
    llm_id = CharField(max_length=128, null=False, help_text="default llm ID", index=True)
    tenant_llm_id = CharField(max_length=32, null=True, help_text="id in tenant_model", index=True)
    embd_id = CharField(max_length=128, null=False, help_text="default embedding model ID", index=True)
    tenant_embd_id = CharField(max_length=32, null=True, help_text="id in tenant_model", index=True)
    asr_id = CharField(max_length=128, null=False, help_text="default ASR model ID", index=True)
    tenant_asr_id = CharField(max_length=32, null=True, help_text="id in tenant_model", index=True)
    img2txt_id = CharField(max_length=128, null=False, help_text="default image to text model ID", index=True)
    tenant_img2txt_id = CharField(max_length=32, null=True, help_text="id in tenant_model", index=True)
    rerank_id = CharField(max_length=128, null=False, help_text="default rerank model ID", index=True)
    tenant_rerank_id = CharField(max_length=32, null=True, help_text="id in tenant_model", index=True)
    tts_id = CharField(max_length=256, null=True, help_text="default tts model ID", index=True)
    tenant_tts_id = CharField(max_length=32, null=True, help_text="id in tenant_model", index=True)
    ocr_id = CharField(max_length=256, null=True, help_text="default OCR model ID", index=True)
    tenant_ocr_id = CharField(max_length=32, null=True, help_text="id in tenant_model", index=True)
    parser_ids = CharField(max_length=256, null=False, help_text="document processors", index=True)
    credit = IntegerField(default=512, index=True)
    status = CharField(max_length=1, null=True, help_text="is it validate(0: wasted, 1: validate)", default="1", index=True)

    class Meta:
        db_table = "tenant"


class UserTenant(DataBaseModel):
    id = CharField(max_length=32, primary_key=True)
    user_id = CharField(max_length=32, null=False, index=True)
    tenant_id = CharField(max_length=32, null=False, index=True)
    role = CharField(max_length=32, null=False, help_text="UserTenantRole", index=True)
    invited_by = CharField(max_length=32, null=False, index=True)
    status = CharField(max_length=1, null=True, help_text="is it validate(0: wasted, 1: validate)", default="1", index=True)

    class Meta:
        db_table = "user_tenant"


class InvitationCode(DataBaseModel):
    id = CharField(max_length=32, primary_key=True)
    code = CharField(max_length=32, null=False, index=True)
    visit_time = DateTimeField(null=True, index=True)
    user_id = CharField(max_length=32, null=True, index=True)
    tenant_id = CharField(max_length=32, null=True, index=True)
    status = CharField(max_length=1, null=True, help_text="is it validate(0: wasted, 1: validate)", default="1", index=True)

    class Meta:
        db_table = "invitation_code"


class LLMFactories(DataBaseModel):
    name = CharField(max_length=128, null=False, help_text="LLM factory name", primary_key=True)
    logo = TextField(null=True, help_text="llm logo base64")
    tags = CharField(max_length=255, null=False, help_text="LLM, Text Embedding, Image2Text, ASR", index=True)
    rank = IntegerField(default=0, index=False)
    status = CharField(max_length=1, null=True, help_text="is it validate(0: wasted, 1: validate)", default="1", index=True)

    def __str__(self):
        return self.name

    class Meta:
        db_table = "llm_factories"


class LLM(DataBaseModel):
    # LLMs dictionary
    llm_name = CharField(max_length=128, null=False, help_text="LLM name", index=True)
    model_type = CharField(max_length=128, null=False, help_text="LLM, Text Embedding, Image2Text, ASR", index=True)
    fid = CharField(max_length=128, null=False, help_text="LLM factory id", index=True)
    max_tokens = IntegerField(default=0)

    tags = CharField(max_length=255, null=False, help_text="LLM, Text Embedding, Image2Text, Chat, 32k...", index=True)
    is_tools = BooleanField(null=False, help_text="support tools", default=False)
    status = CharField(max_length=1, null=True, help_text="is it validate(0: wasted, 1: validate)", default="1", index=True)

    def __str__(self):
        return self.llm_name

    class Meta:
        primary_key = CompositeKey("fid", "llm_name")
        db_table = "llm"


class TenantLLM(DataBaseModel):
    tenant_id = CharField(max_length=32, null=False, index=True)
    llm_factory = CharField(max_length=128, null=False, help_text="LLM factory name", index=True)
    model_type = CharField(max_length=128, null=True, help_text="LLM, Text Embedding, Image2Text, ASR", index=True)
    llm_name = CharField(max_length=128, null=True, help_text="LLM name", default="", index=True)
    api_key = TextField(null=True, help_text="API KEY")
    api_base = CharField(max_length=255, null=True, help_text="API Base")
    max_tokens = IntegerField(default=8192, help_text="Max context token num", index=True)
    used_tokens = IntegerField(default=0, help_text="Used token num", index=True)
    status = CharField(max_length=1, null=False, help_text="is it validate(0: wasted, 1: validate)", default="1", index=True)

    def __str__(self):
        return self.llm_name

    class Meta:
        db_table = "tenant_llm"
        indexes = ((("tenant_id", "llm_factory", "llm_name"), True),)


class TenantLangfuse(DataBaseModel):
    tenant_id = CharField(max_length=32, null=False, primary_key=True)
    secret_key = CharField(max_length=2048, null=False, help_text="SECRET KEY")
    public_key = CharField(max_length=2048, null=False, help_text="PUBLIC KEY")
    host = CharField(max_length=128, null=False, help_text="HOST")

    def __str__(self):
        return "Langfuse host" + self.host

    class Meta:
        db_table = "tenant_langfuse"


class Knowledgebase(DataBaseModel):
    id = CharField(max_length=32, primary_key=True)
    avatar = TextField(null=True, help_text="avatar base64 string")
    tenant_id = CharField(max_length=32, null=False, index=True)
    name = CharField(max_length=128, null=False, help_text="KB name", index=True)
    language = CharField(max_length=32, null=True, default="Chinese" if "zh_CN" in os.getenv("LANG", "") else "English", help_text="English|Chinese", index=True)
    description = TextField(null=True, help_text="KB description")
    embd_id = CharField(max_length=128, null=False, help_text="default embedding model ID", index=True)
    tenant_embd_id = CharField(max_length=32, null=True, help_text="id in tenant_model", index=True)
    permission = CharField(max_length=16, null=False, help_text="me|team", default="me", index=True)
    created_by = CharField(max_length=32, null=False, index=True)
    doc_num = IntegerField(default=0, index=True)
    token_num = IntegerField(default=0, index=True)
    chunk_num = IntegerField(default=0, index=True)
    similarity_threshold = FloatField(default=0.2, index=True)
    vector_similarity_weight = FloatField(default=0.3, index=True)

    parser_id = CharField(max_length=32, null=False, help_text="default parser ID", default=ParserType.NAIVE.value, index=True)
    pipeline_id = CharField(max_length=32, null=True, help_text="Pipeline ID", index=True)
    parser_config = JSONField(null=False, default={"pages": [[1, 1000000]], "table_context_size": 0, "image_context_size": 0})
    pagerank = IntegerField(default=0, index=False)


    status = CharField(max_length=1, null=True, help_text="is it validate(0: wasted, 1: validate)", default="1", index=True)

    def __str__(self):
        return self.name

    class Meta:
        db_table = "knowledgebase"


class Document(DataBaseModel):
    id = CharField(max_length=32, primary_key=True)
    thumbnail = TextField(null=True, help_text="thumbnail base64 string")
    kb_id = CharField(max_length=256, null=False, index=True)
    parser_id = CharField(max_length=32, null=False, help_text="default parser ID", index=True)
    pipeline_id = CharField(max_length=32, null=True, help_text="pipeline ID", index=True)
    parser_config = JSONField(null=False, default={"pages": [[1, 1000000]], "table_context_size": 0, "image_context_size": 0})
    source_type = CharField(max_length=128, null=False, default="local", help_text="where dose this document come from", index=True)
    type = CharField(max_length=32, null=False, help_text="file extension", index=True)
    created_by = CharField(max_length=32, null=False, help_text="who created it", index=True)
    name = CharField(max_length=255, null=True, help_text="file name", index=True)
    location = CharField(max_length=255, null=True, help_text="where dose it store", index=True)
    size = BigIntegerField(default=0, index=True)
    token_num = IntegerField(default=0, index=True)
    chunk_num = IntegerField(default=0, index=True)
    progress = FloatField(default=0, index=True)
    progress_msg = TextField(null=True, help_text="process message", default="")
    process_begin_at = DateTimeField(null=True, index=True)
    process_duration = FloatField(default=0)
    suffix = CharField(max_length=32, null=False, help_text="The real file extension suffix", index=True)

    content_hash = CharField(max_length=32, null=True, help_text="xxhash128 of document content for change detection", default="", index=True)
    # NULL preserves the legacy active-set contract: existing chunks without a
    # chunk_set_id remain searchable until this document completes its first
    # parser-platform activation.
    active_chunk_set_id = CharField(max_length=32, null=True, help_text="currently searchable parser-platform chunk set", index=True)
    requested_parse_run_id = CharField(max_length=32, null=True, help_text="latest requested parser-platform run", index=True)

    run = CharField(max_length=1, null=True, help_text="start to run processing or cancel.(1: run it; 2: cancel)", default="0", index=True)
    status = CharField(max_length=1, null=True, help_text="is it validate(0: wasted, 1: validate)", default="1", index=True)

    class Meta:
        db_table = "document"


class File(DataBaseModel):
    id = CharField(max_length=32, primary_key=True)
    parent_id = CharField(max_length=32, null=False, help_text="parent folder id", index=True)
    tenant_id = CharField(max_length=32, null=False, help_text="tenant id", index=True)
    created_by = CharField(max_length=32, null=False, help_text="who created it", index=True)
    name = CharField(max_length=255, null=False, help_text="file name or folder name", index=True)
    location = CharField(max_length=255, null=True, help_text="where dose it store", index=True)
    size = BigIntegerField(default=0, index=True)
    type = CharField(max_length=32, null=False, help_text="file extension", index=True)
    source_type = CharField(max_length=128, null=False, default="", help_text="where dose this document come from", index=True)

    class Meta:
        db_table = "file"


class File2Document(DataBaseModel):
    id = CharField(max_length=32, primary_key=True)
    file_id = CharField(max_length=32, null=True, help_text="file id", index=True)
    document_id = CharField(max_length=32, null=True, help_text="document id", index=True)

    class Meta:
        db_table = "file2document"


class FileCommit(DataBaseModel):
    id = CharField(max_length=32, primary_key=True)
    folder_id = CharField(max_length=32, null=False, help_text="workspace folder id", index=True)
    parent_id = CharField(max_length=32, null=True, help_text="parent commit id", index=True)
    message = CharField(max_length=512, default="", help_text="commit message")
    author_id = CharField(max_length=32, null=False, help_text="user who created the commit", index=True)
    file_count = IntegerField(default=0, help_text="number of files in this commit")
    tree_state = LongTextField(null=True, help_text="JSON snapshot of the full folder tree at this commit")
    class Meta:
        db_table = "file_commit"


class FileCommitItem(DataBaseModel):
    id = CharField(max_length=32, primary_key=True)
    commit_id = CharField(max_length=32, null=False, help_text="commit id", index=True)
    file_id = CharField(max_length=32, null=False, help_text="file id", index=True)
    operation = CharField(max_length=16, null=False, help_text="add / modify / delete / rename", index=True)
    old_hash = CharField(max_length=64, null=True, help_text="old content hash", index=True)
    new_hash = CharField(max_length=64, null=True, help_text="new content hash", index=True)
    old_location = CharField(max_length=255, null=True, help_text="old storage location")
    new_location = CharField(max_length=255, null=True, help_text="new storage location")
    old_name = CharField(max_length=255, null=True, help_text="old file name (for rename)")
    new_name = CharField(max_length=255, null=True, help_text="new file name (for rename)")
    class Meta:
        db_table = "file_commit_item"
        indexes = (
            (("commit_id", "file_id"), True),  # unique composite index
        )


class Task(DataBaseModel):
    id = CharField(max_length=32, primary_key=True)
    doc_id = CharField(max_length=32, null=False, index=True)
    from_page = IntegerField(default=0)
    to_page = IntegerField(default=MAXIMUM_TASK_PAGE_NUMBER)
    task_type = CharField(max_length=32, null=False, default="")
    priority = IntegerField(default=0)

    begin_at = DateTimeField(null=True, index=True)
    process_duration = FloatField(default=0)

    progress = FloatField(default=0, index=True)
    progress_msg = TextField(null=True, help_text="process message", default="")
    retry_count = IntegerField(default=0)
    digest = TextField(null=True, help_text="task digest", default="")
    chunk_ids = LongTextField(null=True, help_text="chunk ids", default="")
    parse_run_id = CharField(max_length=32, null=True, help_text="parser-platform run identity", index=True)
    chunk_set_id = CharField(max_length=32, null=True, help_text="run-scoped staging chunk set", index=True)


class ParserRun(DataBaseModel):
    id = CharField(max_length=32, primary_key=True)
    doc_id = CharField(max_length=32, null=False, index=True)
    chunk_set_id = CharField(max_length=32, null=False, unique=True)
    idempotency_key = CharField(max_length=64, null=False, unique=True)
    source_hash = CharField(max_length=64, null=False, index=True)
    source_format = CharField(max_length=16, null=False, index=True)
    source_fingerprint = CharField(max_length=64, null=False, index=True)
    config_fingerprint = CharField(max_length=64, null=False, index=True)
    chunking_config = JSONField(null=False, default=dict)
    parser_fingerprint = CharField(max_length=64, null=False, index=True)
    parser_name = CharField(max_length=64, null=False, index=True)
    parser_version = CharField(max_length=64, null=False)
    model_version = CharField(max_length=128, null=True)
    backend = CharField(max_length=64, null=False)
    schema_version = CharField(max_length=32, null=False)
    lifecycle = CharField(max_length=32, null=False, default="QUEUED", index=True)
    warnings = JSONField(null=False, default=list)
    error_code = CharField(max_length=64, null=True, index=True)
    error_message = TextField(null=True)
    raw_artifact_ref = CharField(max_length=1024, null=True)
    expected_task_count = IntegerField(default=1)
    completed_task_count = IntegerField(default=0)
    failed_task_count = IntegerField(default=0)
    expected_page_count = IntegerField(default=0)
    completed_page_count = IntegerField(default=0)
    reused_page_count = IntegerField(default=0)
    failed_page_count = IntegerField(default=0)
    staged_chunk_count = IntegerField(default=0)
    staged_token_count = IntegerField(default=0)
    activated_at = DateTimeField(null=True, index=True)
    retained_until = DateTimeField(null=True, index=True)
    retained_from_lifecycle = CharField(max_length=32, null=True)

    class Meta:
        db_table = "parser_run"
        indexes = (
            (("doc_id", "lifecycle"), False),
            (("doc_id", "source_fingerprint", "config_fingerprint"), False),
        )


class Dialog(DataBaseModel):
    id = CharField(max_length=32, primary_key=True)
    tenant_id = CharField(max_length=32, null=False, index=True)
    name = CharField(max_length=255, null=True, help_text="dialog application name", index=True)
    description = TextField(null=True, help_text="Dialog description")
    icon = TextField(null=True, help_text="icon base64 string")
    language = CharField(max_length=32, null=True, default="Chinese" if "zh_CN" in os.getenv("LANG", "") else "English", help_text="English|Chinese", index=True)
    llm_id = CharField(max_length=128, null=False, help_text="default llm ID")
    tenant_llm_id = CharField(max_length=32, null=True, help_text="id in tenant_model", index=True)

    llm_setting = JSONField(null=False, default={"temperature": 0.1, "top_p": 0.3, "frequency_penalty": 0.7, "presence_penalty": 0.4, "max_tokens": 512})
    prompt_type = CharField(max_length=16, null=False, default="simple", help_text="simple|advanced", index=True)
    prompt_config = JSONField(
        null=False,
        default={"system": "", "prologue": "Hi! I'm your assistant. What can I do for you?", "parameters": [], "empty_response": "Sorry! No relevant content was found in the knowledge base!"},
    )
    meta_data_filter = JSONField(null=True, default={})

    similarity_threshold = FloatField(default=0.2)
    vector_similarity_weight = FloatField(default=0.3)

    top_n = IntegerField(default=6)
    rerank_candidates_count = IntegerField(default=64)

    top_k = IntegerField(default=1024)

    do_refer = CharField(max_length=1, null=False, default="1", help_text="it needs to insert reference index into answer or not")

    rerank_id = CharField(max_length=128, null=False, help_text="default rerank model ID")
    tenant_rerank_id = CharField(max_length=32, null=True, help_text="id in tenant_model", index=True)
    kb_ids = JSONField(null=False, default=[])
    status = CharField(max_length=1, null=True, help_text="is it validate(0: wasted, 1: validate)", default="1", index=True)

    class Meta:
        db_table = "dialog"


class Conversation(DataBaseModel):
    id = CharField(max_length=32, primary_key=True)
    dialog_id = CharField(max_length=32, null=False, index=True)
    name = CharField(max_length=255, null=True, help_text="conversation name", index=True)
    message = JSONField(null=True)
    reference = JSONField(null=True, default=[])
    user_id = CharField(max_length=255, null=True, help_text="user_id", index=True)

    class Meta:
        db_table = "conversation"


class APIToken(DataBaseModel):
    tenant_id = CharField(max_length=32, null=False, index=True)
    token = CharField(max_length=255, null=False, index=True)
    dialog_id = CharField(max_length=32, null=True, index=True)
    source = CharField(max_length=16, null=True, help_text="none|dialog", index=True)
    beta = CharField(max_length=255, null=True, index=True)

    class Meta:
        db_table = "api_token"
        primary_key = CompositeKey("tenant_id", "token")


class API4Conversation(DataBaseModel):
    id = CharField(max_length=32, primary_key=True)
    name = CharField(max_length=255, null=True, help_text="conversation name", index=False)
    dialog_id = CharField(max_length=32, null=False, index=True)
    user_id = CharField(max_length=255, null=False, help_text="user_id", index=True)
    exp_user_id = CharField(max_length=255, null=True, help_text="exp_user_id", index=True)
    message = JSONField(null=True)
    reference = JSONField(null=True, default=[])
    tokens = IntegerField(default=0)
    source = CharField(max_length=16, null=True, help_text="none|dialog", index=True)
    duration = FloatField(default=0, index=True)
    round = IntegerField(default=0, index=True)
    thumb_up = IntegerField(default=0, index=True)

    class Meta:
        db_table = "api_4_conversation"


class DocmindProject(DataBaseModel):
    id = CharField(max_length=32, primary_key=True)
    tenant_id = CharField(max_length=32, null=False, index=True)
    dataset_id = CharField(max_length=32, null=False, index=True)
    active_version_id = CharField(max_length=32, null=True, index=True)
    source_root_file_id = CharField(max_length=32, null=True, index=True)
    catalog_source_mode = CharField(max_length=16, null=False, default="static", index=True)
    lock_version = IntegerField(default=0, null=False)

    class Meta:
        db_table = "docmind_project"
        indexes = ((('tenant_id', 'dataset_id'), True),)


class DocmindFolder(DataBaseModel):
    id = CharField(max_length=32, primary_key=True)
    project_id = CharField(max_length=32, null=False, index=True)
    slug = CharField(max_length=64, null=False)
    display_name = CharField(max_length=128, null=False)
    ordinal = IntegerField(null=False)
    enabled = BooleanField(default=True, null=False)
    source_file_id = CharField(max_length=32, null=True, index=True)

    class Meta:
        db_table = "docmind_folder"
        indexes = (
            (("project_id", "slug"), True),
            (("project_id", "ordinal"), True),
        )


class DocmindRegistration(DataBaseModel):
    id = CharField(max_length=32, primary_key=True)
    project_id = CharField(max_length=32, null=False, index=True)
    folder_id = CharField(max_length=32, null=False, index=True)
    document_id = CharField(max_length=32, null=False, index=True)
    file_id = CharField(max_length=32, null=True, index=True)
    captured_content_hash = CharField(max_length=64, null=True)
    lifecycle_state = CharField(max_length=32, null=False, index=True)
    error_code = CharField(max_length=64, null=True, index=True)
    error_message = TextField(null=True)
    created_by = CharField(max_length=32, null=False, index=True)
    retry_of_id = CharField(max_length=32, null=True, index=True)

    class Meta:
        db_table = "docmind_registration"


class DocmindRegistrationCurrent(DataBaseModel):
    id = CharField(max_length=32, primary_key=True)
    project_id = CharField(max_length=32, null=False, index=True)
    document_id = CharField(max_length=32, null=False, index=True)
    registration_id = CharField(max_length=32, null=False, index=True)
    lock_version = IntegerField(default=0, null=False)

    class Meta:
        db_table = "docmind_registration_current"
        indexes = ((('project_id', 'document_id'), True),)


class DocmindSource(DataBaseModel):
    """Logical source registry. Physical roots remain host-local configuration."""

    id = CharField(max_length=64, primary_key=True)
    project_id = CharField(max_length=32, null=False, index=True)
    display_name = CharField(max_length=128, null=False)
    default_folder_id = CharField(max_length=32, null=True, index=True)
    enabled = BooleanField(default=True, null=False, index=True)

    class Meta:
        db_table = "docmind_source"
        indexes = ((('project_id', 'id'), True),)


class DocmindSourceSyncSession(DataBaseModel):
    """One durable fence per source; never prune epochs or sequence watermarks."""

    source_id = CharField(max_length=64, primary_key=True)
    worker_id = CharField(max_length=128, null=False)
    owner_id = CharField(max_length=128, null=False)
    epoch = BigIntegerField(default=1, null=False)
    last_sequence = BigIntegerField(default=0, null=False)
    lease_expires_at = DateTimeField(null=False, index=True)

    class Meta:
        db_table = "docmind_source_sync_session"


class DocmindSourceChangeReceipt(DataBaseModel):
    """Durable result committed in the same transaction as document mutations."""

    id = CharField(max_length=32, primary_key=True)
    source_id = CharField(max_length=64, null=False)
    epoch = BigIntegerField(null=False)
    sequence = BigIntegerField(null=False)
    request_id = CharField(max_length=128, null=False)
    payload_sha256 = CharField(max_length=64, null=False)
    result_json = LongTextField(null=False)
    received_at = DateTimeField(null=False, index=True)

    class Meta:
        db_table = "docmind_source_change_receipt"
        indexes = (
            (("source_id", "epoch", "request_id"), True),
            (("source_id", "epoch", "sequence"), True),
        )


class DocmindSourceChangeObservation(DataBaseModel):
    """An actual observation remains distinct from HTTP delivery attempts."""

    id = CharField(max_length=64, primary_key=True)
    source_id = CharField(max_length=64, null=False)
    epoch = BigIntegerField(null=False)
    observation_id = CharField(max_length=128, null=False)
    payload_sha256 = CharField(max_length=64, null=False)
    result_json = LongTextField(null=False)
    received_at = DateTimeField(null=False, index=True)

    class Meta:
        db_table = "docmind_source_change_observation"
        indexes = ((("source_id", "epoch", "observation_id"), True),)


class DocmindSourceDocument(DataBaseModel):
    """Stable cloud-source identity; never stores a host physical root."""

    id = CharField(max_length=32, primary_key=True)
    project_id = CharField(max_length=32, null=False, index=True)
    source_id = CharField(max_length=64, null=False, index=True)
    document_id = CharField(max_length=32, null=False, unique=True, index=True)
    source_object_id = CharField(max_length=255, null=True, index=True)
    host_file_identity = CharField(max_length=255, null=True, index=True)
    folder_id = CharField(max_length=32, null=False, index=True)
    relative_path = CharField(max_length=1024, null=False)
    relative_path_hash = CharField(max_length=64, null=False, index=True)
    active_source_version_id = CharField(max_length=32, null=True, index=True)
    observed_ciphertext_sha256 = CharField(max_length=64, null=True, index=True)
    observed_size = BigIntegerField(null=True)
    observed_mtime_ns = BigIntegerField(null=True)
    stable_observation_count = IntegerField(default=0, null=False)
    source_dirty = BooleanField(default=False, null=False)
    observation_epoch = BigIntegerField(default=0, null=False)
    observation_generation = BigIntegerField(default=0, null=False)
    last_change_sequence = BigIntegerField(default=0, null=False)
    last_change_received_at = DateTimeField(null=True, index=True)
    stable_observed_at = DateTimeField(null=True)
    latest_target_version_id = CharField(max_length=32, null=True)
    generation = BigIntegerField(default=0, null=False)
    deleted_at = DateTimeField(null=True, index=True)

    class Meta:
        db_table = "docmind_source_document"
        indexes = (
            (("project_id", "source_id", "relative_path_hash"), True),
            (("project_id", "source_id", "document_id"), True),
            (("project_id", "source_id", "host_file_identity"), False),
            (("project_id", "source_id", "id"), False),
        )


class DocmindSourceVersion(DataBaseModel):
    id = CharField(max_length=32, primary_key=True)
    source_document_id = CharField(max_length=32, null=False, index=True)
    document_id = CharField(max_length=32, null=False, index=True)
    ciphertext_sha256 = CharField(max_length=64, null=False, index=True)
    ciphertext_size = BigIntegerField(null=False)
    source_mtime_ns = BigIntegerField(null=False)
    content_sha256 = CharField(max_length=64, null=True, index=True)
    parser_run_id = CharField(max_length=32, null=True, index=True)
    chunk_set_id = CharField(max_length=32, null=True, index=True)
    lifecycle_state = CharField(max_length=32, null=False, index=True)
    activated_at = DateTimeField(null=True, index=True)
    search_cleanup_complete = BooleanField(default=False, null=False)

    class Meta:
        db_table = "docmind_source_version"
        indexes = (
            (("source_document_id", "ciphertext_sha256", "source_mtime_ns"), True),
        )


class DocmindIngestionJob(DataBaseModel):
    id = CharField(max_length=32, primary_key=True)
    project_id = CharField(max_length=32, null=False, index=True)
    source_id = CharField(max_length=64, null=False, index=True)
    source_document_id = CharField(max_length=32, null=False, index=True)
    document_id = CharField(max_length=32, null=False, index=True)
    version_id = CharField(max_length=32, null=False, unique=True, index=True)
    idempotency_key = CharField(max_length=128, null=False, unique=True)
    lifecycle_state = CharField(max_length=32, null=False, index=True)
    attempt = IntegerField(default=0, null=False)
    fencing_token = BigIntegerField(default=0, null=False)
    observation_epoch = BigIntegerField(default=0, null=False)
    observation_generation = BigIntegerField(default=0, null=False)
    lease_owner = CharField(max_length=128, null=True, index=True)
    lease_expires_at = DateTimeField(null=True, index=True)
    retry_not_before = DateTimeField(null=True, index=True)
    parser_input_token_hash = CharField(max_length=64, null=True)
    plaintext_sha256 = CharField(max_length=64, null=True)
    plaintext_size = BigIntegerField(null=True)
    parser_run_id = CharField(max_length=32, null=True, index=True)
    chunk_set_id = CharField(max_length=32, null=True, index=True)
    host_cleanup_state = CharField(max_length=32, null=False, default="NOT_STARTED", index=True)
    cleanup_state = CharField(max_length=32, null=False, default="NOT_STARTED", index=True)
    error_code = CharField(max_length=64, null=True, index=True)
    error_message = TextField(null=True)
    pdf_ocr_requested = BooleanField(default=False, null=False)
    pdf_ocr_consented_by = CharField(max_length=32, null=True)
    pdf_ocr_consented_at = DateTimeField(null=True)
    pdf_ocr_consent_key_hash = CharField(max_length=64, null=True)

    class Meta:
        db_table = "docmind_ingestion_job"
        indexes = (
            (("project_id", "source_id", "lifecycle_state"), False),
            (("source_document_id", "fencing_token"), False),
        )


class DocmindPreviewSession(DataBaseModel):
    """Short-lived, owner-bound preview metadata; file contents stay in tmpfs."""

    id = CharField(max_length=32, primary_key=True)
    owner_id = CharField(max_length=32, null=False, index=True)
    project_id = CharField(max_length=32, null=False, index=True)
    source_id = CharField(max_length=64, null=False, index=True)
    source_document_id = CharField(max_length=32, null=False, index=True)
    document_id = CharField(max_length=32, null=False, index=True)
    version_id = CharField(max_length=32, null=False, index=True)
    requested_chunk_set_id = CharField(max_length=32, null=False)
    idempotency_hash = CharField(max_length=64, null=False, unique=True)
    token_hash = CharField(max_length=64, null=False)
    lifecycle_state = CharField(max_length=32, null=False, default="QUEUED", index=True)
    source_format = CharField(max_length=16, null=False)
    display_format = CharField(max_length=16, null=True)
    viewer_kind = CharField(max_length=16, null=True)
    page_count = IntegerField(null=True)
    reserved_bytes = BigIntegerField(null=False)
    artifact_bytes = BigIntegerField(null=True)
    fencing_token = BigIntegerField(default=0, null=False)
    lease_owner = CharField(max_length=128, null=True)
    lease_expires_at = DateTimeField(null=True, index=True)
    host_cleanup_state = CharField(max_length=32, null=False, default="NOT_STARTED")
    cleanup_state = CharField(max_length=32, null=False, default="NOT_STARTED")
    error_code = CharField(max_length=64, null=True)
    last_heartbeat_at = DateTimeField(null=False)
    expires_at = DateTimeField(null=False, index=True)
    hard_expires_at = DateTimeField(null=False)
    active_readers = IntegerField(default=0, null=False)
    reader_lease_expires_at = DateTimeField(null=True)

    class Meta:
        db_table = "docmind_preview_session"
        indexes = ((('owner_id', 'document_id', 'version_id'), False),)


class DocmindWorkerRequestNonce(DataBaseModel):
    id = CharField(max_length=64, primary_key=True)
    key_id = CharField(max_length=128, null=False, index=True)
    nonce_hash = CharField(max_length=64, null=False, unique=True)
    expires_at = DateTimeField(null=False, index=True)

    class Meta:
        db_table = "docmind_worker_request_nonce"


class DocmindSourceScan(DataBaseModel):
    """One source-scoped, resumable full-scan/reconciliation attempt."""

    id = CharField(max_length=32, primary_key=True)
    project_id = CharField(max_length=32, null=False, index=True)
    source_id = CharField(max_length=64, null=False, index=True)
    scan_id = CharField(max_length=64, null=False)
    worker_id = CharField(max_length=128, null=False, index=True)
    protocol_version = IntegerField(default=1, null=False)
    scan_start_epoch = BigIntegerField(default=0, null=False)
    scan_start_sequence = BigIntegerField(default=0, null=False)
    schedule_fencing_token = BigIntegerField(null=True)
    trigger = CharField(max_length=32, null=False, index=True)
    lifecycle_state = CharField(max_length=32, null=False, index=True)
    root_access_confirmed = BooleanField(default=False, null=False)
    received_batch_count = IntegerField(default=0, null=False)
    observed_file_count = BigIntegerField(default=0, null=False)
    declared_batch_count = IntegerField(null=True)
    declared_file_count = BigIntegerField(null=True)
    started_at = DateTimeField(null=False, index=True)
    completed_at = DateTimeField(null=True, index=True)
    error_code = CharField(max_length=64, null=True, index=True)

    class Meta:
        db_table = "docmind_source_scan"
        indexes = (
            (("source_id", "scan_id"), True),
            (("project_id", "source_id", "lifecycle_state"), False),
        )


class DocmindSourceScanBatch(DataBaseModel):
    id = CharField(max_length=32, primary_key=True)
    source_scan_id = CharField(max_length=32, null=False, index=True)
    batch_index = IntegerField(null=False)
    payload_hash = CharField(max_length=64, null=False)
    item_count = IntegerField(default=0, null=False)

    class Meta:
        db_table = "docmind_source_scan_batch"
        indexes = ((('source_scan_id', 'batch_index'), True),)


class DocmindSourceScanEntry(DataBaseModel):
    id = CharField(max_length=32, primary_key=True)
    source_scan_id = CharField(max_length=32, null=False, index=True)
    relative_path = CharField(max_length=1024, null=False)
    relative_path_hash = CharField(max_length=64, null=False, index=True)
    ciphertext_sha256 = CharField(max_length=64, null=False, index=True)
    ciphertext_size = BigIntegerField(null=False)
    source_mtime_ns = BigIntegerField(null=False)
    host_file_identity = CharField(max_length=255, null=True, index=True)
    source_document_id = CharField(max_length=32, null=True, index=True)
    reconciliation_state = CharField(max_length=32, null=False, index=True)

    class Meta:
        db_table = "docmind_source_scan_entry"
        indexes = ((('source_scan_id', 'relative_path_hash'), True),)


class DocmindSourceDeletion(DataBaseModel):
    """Search exclusion tombstone retained with metadata for thirty days."""

    id = CharField(max_length=32, primary_key=True)
    project_id = CharField(max_length=32, null=False, index=True)
    source_id = CharField(max_length=64, null=False, index=True)
    source_document_id = CharField(max_length=32, null=False, index=True)
    document_id = CharField(max_length=32, null=False, index=True)
    authority_kind = CharField(max_length=32, null=False, index=True)
    authority_scan_id = CharField(max_length=32, null=True, index=True)
    deletion_generation = BigIntegerField(default=0, null=False)
    confirmed_at = DateTimeField(null=False, index=True)
    search_excluded_at = DateTimeField(null=True, index=True)
    retained_until = DateTimeField(null=False, index=True)
    lifecycle_state = CharField(max_length=32, null=False, index=True)

    class Meta:
        db_table = "docmind_source_deletion"
        indexes = ((('source_document_id', 'confirmed_at'), True),)


class DocmindSourceReconciliationSchedule(DataBaseModel):
    id = CharField(max_length=32, primary_key=True)
    project_id = CharField(max_length=32, null=False, index=True)
    source_id = CharField(max_length=64, null=False, unique=True, index=True)
    timezone_name = CharField(max_length=64, null=False, default="Asia/Seoul")
    next_due_at = DateTimeField(null=False, index=True)
    pending_local_date = CharField(max_length=10, null=True, index=True)
    last_claimed_local_date = CharField(max_length=10, null=True, index=True)
    lease_owner = CharField(max_length=128, null=True, index=True)
    lease_expires_at = DateTimeField(null=True, index=True)
    fencing_token = BigIntegerField(default=0, null=False)

    class Meta:
        db_table = "docmind_source_reconciliation_schedule"


class DocmindSourceFolder(DataBaseModel):
    """Verified source folder identity without overloading catalog folders."""

    id = CharField(max_length=32, primary_key=True)
    project_id = CharField(max_length=32, null=False, index=True)
    source_id = CharField(max_length=64, null=False, index=True)
    folder_id = CharField(max_length=32, null=False, index=True)
    parent_source_folder_id = CharField(max_length=32, null=True, index=True)
    relative_path = CharField(max_length=1024, null=False)
    relative_path_hash = CharField(max_length=64, null=False, index=True)
    source_object_id = CharField(max_length=255, null=True, index=True)
    verified = BooleanField(default=False, null=False, index=True)

    class Meta:
        db_table = "docmind_source_folder"
        indexes = (
            (("project_id", "source_id", "relative_path_hash"), True),
            (("source_id", "source_object_id"), False),
        )


class DocmindSourceOperation(DataBaseModel):
    """Durable source mutation intent; source and indexing completion are separate."""

    id = CharField(max_length=32, primary_key=True)
    project_id = CharField(max_length=32, null=False, index=True)
    actor_id = CharField(max_length=32, null=False, index=True)
    operation = CharField(max_length=16, null=False, index=True)
    idempotency_key = CharField(max_length=128, null=False)
    request_hash = CharField(max_length=64, null=False)
    source_id = CharField(max_length=64, null=False, index=True)
    source_document_id = CharField(max_length=32, null=True, index=True)
    document_id = CharField(max_length=32, null=True, index=True)
    destination_source_id = CharField(max_length=64, null=True, index=True)
    destination_folder_id = CharField(max_length=32, null=True, index=True)
    destination_relative_path = CharField(max_length=1024, null=True)
    expected_source_version_id = CharField(max_length=32, null=True)
    lifecycle_state = CharField(max_length=32, null=False, index=True)
    source_operation_state = CharField(max_length=32, null=False, index=True)
    indexing_state = CharField(max_length=32, null=False, index=True)
    attempt = IntegerField(default=0, null=False)
    fencing_token = BigIntegerField(default=0, null=False)
    lease_owner = CharField(max_length=128, null=True, index=True)
    lease_expires_at = DateTimeField(null=True, index=True)
    result_source_document_id = CharField(max_length=32, null=True, index=True)
    provider_operation_id = CharField(max_length=128, null=True, index=True)
    confirmed_relative_path = CharField(max_length=1024, null=True)
    confirmed_source_object_id = CharField(max_length=255, null=True, index=True)
    error_code = CharField(max_length=64, null=True, index=True)
    completed_at = DateTimeField(null=True, index=True)

    class Meta:
        db_table = "docmind_source_operation"
        indexes = (
            (("project_id", "actor_id", "idempotency_key"), True),
            (("source_id", "lifecycle_state"), False),
        )


class DocmindDocumentRoutingDigest(DataBaseModel):
    id = CharField(max_length=32, primary_key=True)
    project_id = CharField(max_length=32, null=False, index=True)
    document_id = CharField(max_length=32, null=False, index=True)
    input_identity_hash = CharField(max_length=64, null=False, index=True)
    content_hash = CharField(max_length=64, null=False)
    chunk_set_fingerprint = CharField(max_length=64, null=False)
    dataset_acl_scope_hash = CharField(max_length=64, null=False)
    model_version = CharField(max_length=128, null=False)
    prompt_version = CharField(max_length=128, null=False)
    config_version = CharField(max_length=128, null=False)
    output_json = LongTextField(null=True)
    output_hash = CharField(max_length=64, null=True)
    status = CharField(max_length=32, null=False, index=True)
    error_code = CharField(max_length=64, null=True, index=True)
    invalidated_at = DateTimeField(null=True, index=True)

    class Meta:
        db_table = "docmind_document_routing_digest"
        indexes = ((('project_id', 'document_id', 'input_identity_hash'), True),)


class DocmindCatalogVersion(DataBaseModel):
    id = CharField(max_length=32, primary_key=True)
    project_id = CharField(max_length=32, null=False, index=True)
    parent_version_id = CharField(max_length=32, null=True, index=True)
    version_label = CharField(max_length=64, null=False)
    lifecycle_state = CharField(max_length=32, null=False, index=True)
    health_state = CharField(max_length=32, null=False, index=True)
    health_reason = CharField(max_length=64, null=True, index=True)
    snapshot_hash = CharField(max_length=64, null=False, index=True)
    snapshot_json = LongTextField(null=False)
    root_uri = CharField(max_length=255, null=False)
    root_version = CharField(max_length=64, null=True)
    routing_card_set_hash = CharField(max_length=64, null=True)
    validation_report_hash = CharField(max_length=64, null=True)
    validated_at = DateTimeField(null=True, index=True)
    validation_expires_at = DateTimeField(null=True, index=True)
    readiness_mode = CharField(
        max_length=32,
        null=False,
        default="SEARCH_VALIDATED",
        index=True,
    )
    source_ready_version_id = CharField(max_length=32, null=True, index=True)
    manual_saved_by = CharField(max_length=32, null=True, index=True)
    manual_saved_at = DateTimeField(null=True, index=True)
    created_by = CharField(max_length=32, null=True, index=True)
    published_by = CharField(max_length=32, null=True, index=True)
    published_at = DateTimeField(null=True, index=True)
    rolled_back_by = CharField(max_length=32, null=True, index=True)
    rolled_back_at = DateTimeField(null=True, index=True)
    snapshot_schema_version = IntegerField(default=1, null=False, index=True)
    source_tree_hash = CharField(max_length=64, null=True, index=True)
    root_identity_sha256 = CharField(max_length=64, null=True, index=True)

    class Meta:
        db_table = "docmind_catalog_version"
        indexes = ((('project_id', 'version_label'), True),)


class DocmindFolderVersion(DataBaseModel):
    id = CharField(max_length=32, primary_key=True)
    version_id = CharField(max_length=32, null=False, index=True)
    folder_id = CharField(max_length=32, null=False, index=True)
    l0_text = TextField(null=True)
    l1_text = TextField(null=True)
    l0_hash = CharField(max_length=64, null=True)
    l1_hash = CharField(max_length=64, null=True)
    generator_metadata = JSONField(null=True, default={})
    parent_folder_id = CharField(max_length=32, null=True, index=True)
    source_file_id = CharField(max_length=32, null=True, index=True)
    relative_path = CharField(max_length=1024, null=True)
    display_name = CharField(max_length=255, null=True)
    ordinal = IntegerField(null=True)
    depth = IntegerField(null=True)

    class Meta:
        db_table = "docmind_folder_version"
        indexes = ((('version_id', 'folder_id'), True),)


class DocmindFolderVersionDocument(DataBaseModel):
    id = CharField(max_length=32, primary_key=True)
    version_id = CharField(max_length=32, null=False, index=True)
    folder_id = CharField(max_length=32, null=False, index=True)
    document_id = CharField(max_length=32, null=False, index=True)
    ordinal = IntegerField(null=False)
    captured_content_hash = CharField(max_length=64, null=True)
    routing_digest_id = CharField(max_length=32, null=True, index=True)
    routing_digest_hash = CharField(max_length=64, null=True)
    chunk_set_fingerprint = CharField(max_length=64, null=True)

    class Meta:
        db_table = "docmind_folder_version_document"
        indexes = (
            (("version_id", "document_id"), True),
            (("version_id", "folder_id", "ordinal"), True),
            (("document_id", "version_id"), False),
        )


class DocmindFolderImportJob(DataBaseModel):
    id = CharField(max_length=32, primary_key=True)
    project_id = CharField(max_length=32, null=False, index=True)
    source_root_file_id = CharField(max_length=32, null=False, index=True)
    actor_id = CharField(max_length=32, null=False, index=True)
    idempotency_key = CharField(max_length=128, null=False)
    manifest_hash = CharField(max_length=64, null=False, index=True)
    lifecycle_state = CharField(max_length=32, null=False, index=True)
    item_count = IntegerField(default=0, null=False)
    succeeded_count = IntegerField(default=0, null=False)
    failed_count = IntegerField(default=0, null=False)

    class Meta:
        db_table = "docmind_folder_import_job"
        indexes = ((('project_id', 'actor_id', 'idempotency_key'), True),)


class DocmindFolderImportItem(DataBaseModel):
    id = CharField(max_length=32, primary_key=True)
    job_id = CharField(max_length=32, null=False, index=True)
    relative_path = CharField(max_length=1024, null=False)
    path_hash = CharField(max_length=64, null=False, index=True)
    content_hash = CharField(max_length=64, null=True, index=True)
    lifecycle_state = CharField(max_length=32, null=False, index=True)
    error_code = CharField(max_length=64, null=True, index=True)
    source_file_id = CharField(max_length=32, null=True, index=True)
    document_id = CharField(max_length=32, null=True, index=True)
    registration_id = CharField(max_length=32, null=True, index=True)

    class Meta:
        db_table = "docmind_folder_import_item"
        indexes = ((('job_id', 'path_hash'), True),)


class DocmindDraftChange(DataBaseModel):
    id = CharField(max_length=32, primary_key=True)
    draft_version_id = CharField(max_length=32, null=False, index=True)
    operation = CharField(max_length=16, null=False, index=True)
    document_id = CharField(max_length=32, null=False, index=True)
    registration_id = CharField(max_length=32, null=True, index=True)
    from_folder_id = CharField(max_length=32, null=True, index=True)
    to_folder_id = CharField(max_length=32, null=True, index=True)
    expected_parent_folder_id = CharField(max_length=32, null=True)
    expected_parent_membership_hash = CharField(max_length=64, null=True)
    actor_id = CharField(max_length=32, null=False, index=True)
    ordinal = IntegerField(null=False)

    class Meta:
        db_table = "docmind_draft_change"
        indexes = ((('draft_version_id', 'document_id'), True),)


class DocmindIdempotencyOperation(DataBaseModel):
    id = CharField(max_length=32, primary_key=True)
    project_id = CharField(max_length=32, null=False, index=True)
    actor_id = CharField(max_length=32, null=False, index=True)
    operation = CharField(max_length=64, null=False, index=True)
    idempotency_key = CharField(max_length=128, null=False)
    request_hash = CharField(max_length=64, null=False)
    state = CharField(max_length=32, null=False, index=True)
    result_json = LongTextField(null=True)
    status_code = IntegerField(null=True)

    class Meta:
        db_table = "docmind_idempotency_operation"
        indexes = ((('project_id', 'actor_id', 'operation', 'idempotency_key'), True),)


class DocmindManualCardRevisionClaim(DataBaseModel):
    id = CharField(max_length=32, primary_key=True)
    project_id = CharField(max_length=32, null=False, index=True)
    source_ready_version_id = CharField(max_length=32, null=False, index=True)
    source_snapshot_hash = CharField(max_length=64, null=False, index=True)
    revision_id = CharField(max_length=32, null=True, index=True)
    state = CharField(max_length=32, null=False, index=True)

    class Meta:
        db_table = "docmind_manual_card_revision_claim"
        indexes = (
            (
                (
                    "project_id",
                    "source_ready_version_id",
                    "source_snapshot_hash",
                ),
                True,
            ),
        )


class DocmindAuditEvent(DataBaseModel):
    id = CharField(max_length=32, primary_key=True)
    project_id = CharField(max_length=32, null=False, index=True)
    actor_id = CharField(max_length=32, null=True, index=True)
    action = CharField(max_length=64, null=False, index=True)
    target_type = CharField(max_length=64, null=False)
    target_id = CharField(max_length=64, null=True)
    before_version_id = CharField(max_length=32, null=True)
    after_version_id = CharField(max_length=32, null=True)
    outcome = CharField(max_length=32, null=False, index=True)
    trace_id = CharField(max_length=64, null=True, index=True)
    details = JSONField(null=True, default={})

    class Meta:
        db_table = "docmind_audit_event"


class DateTimeTzField(CharField):
    field_type = "VARCHAR"

    def db_value(self, value: datetime | None) -> str | None:
        if value is not None:
            if value.tzinfo is not None:
                return value.isoformat()
            else:
                return value.replace(tzinfo=timezone.utc).isoformat()
        return value

    def python_value(self, value: str | datetime | None) -> datetime | None:
        if value is None:
            return None
        if isinstance(value, datetime):
            # The column is declared VARCHAR, but deployments upgraded from
            # older schemas may hold it as native DATETIME, in which case the
            # driver returns datetime objects instead of ISO strings.
            return value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)
        try:
            dt = datetime.fromisoformat(value)
        except ValueError:
            # Zero dates (0000-00-00) and other unparseable values must not
            # raise here: peewee counts the row before converting it, so one
            # bad value wedges every later iteration over the table with
            # IndexError: list index out of range.
            logging.warning("DateTimeTzField: unparseable value %r, falling back to None", value)
            return None
        return dt if dt.tzinfo is not None else dt.replace(tzinfo=timezone.utc)


class SystemSettings(DataBaseModel):
    name = CharField(max_length=128, primary_key=True)
    source = CharField(max_length=32, null=False, index=False)
    data_type = CharField(max_length=32, null=False, index=False)
    value = TextField(null=False, help_text="Configuration value (JSON, string, etc.)")

    class Meta:
        db_table = "system_settings"


class TenantModelProvider(DataBaseModel):
    id = CharField(max_length=32, primary_key=True)
    provider_name = CharField(max_length=128, null=False, index=False, help_text="LLM provider name")
    tenant_id = CharField(max_length=32, null=False, index=True)

    class Meta:
        db_table = "tenant_model_provider"
        indexes = ((("tenant_id", "provider_name"), True),)


class TenantModelInstance(DataBaseModel):
    id = CharField(max_length=32, primary_key=True)
    instance_name = CharField(max_length=128, null=False, index=False, help_text="Model instance name")
    provider_id = CharField(max_length=32, null=False, index=False)
    api_key = CharField(max_length=512, null=False, index=False, help_text="API key")
    status = CharField(max_length=32, default="active", index=False)
    extra = CharField(max_length=512, default="{}", index=False)

    class Meta:
        db_table = "tenant_model_instance"


class TenantModel(DataBaseModel):
    id = CharField(max_length=32, primary_key=True)
    model_name = CharField(max_length=128, null=True, index=False, help_text="Model name")
    provider_id = CharField(max_length=32, null=False, index=False)
    instance_id = CharField(max_length=32, null=False, index=True)
    model_type = IntegerField(null=False, default=1, index=True, help_text="Bit flags (LSB->MSB): 1=chat, 2=embedding, 4=asr, 8=vision, 16=rerank, 32=tts, 64=ocr")
    status = CharField(max_length=32, default="active", index=False)
    extra = CharField(max_length=1024, default="{}", index=False)

    class Meta:
        db_table = "tenant_model"


class TenantModelGroup(DataBaseModel):
    id = CharField(max_length=32, primary_key=True)
    group_type = CharField(max_length=32, null=False, index=False, help_text="Group type")
    model_name = CharField(max_length=128, null=True, index=False, help_text="Model name")
    strategy = CharField(max_length=32, default="weighted", index=False, help_text="Routing strategy")

    class Meta:
        db_table = "tenant_model_group"


class TenantModelGroupMapping(DataBaseModel):
    group_id = CharField(max_length=32, null=False, index=True, help_text="Group ID")
    provider_id = CharField(max_length=32, null=False, index=False)
    instance_id = CharField(max_length=32, null=False, index=False)
    model_id = CharField(max_length=32, null=False, index=True)
    weight = IntegerField(default=100, index=False, help_text="Routing weight")
    status = CharField(max_length=32, default="active", index=False)

    class Meta:
        db_table = "tenant_model_group_mapping"
        primary_key = CompositeKey("group_id", "provider_id", "instance_id", "model_id")


def alter_db_add_column(migrator, table_name, column_name, column_type):

    try:
        migrate(migrator.add_column(table_name, column_name, column_type))
    except OperationalError as ex:
        error_codes = [1060]
        error_messages = ["Duplicate column name"]

        should_skip_error = (hasattr(ex, "args") and ex.args and ex.args[0] in error_codes) or (str(ex) in error_messages)

        if not should_skip_error:
            logging.critical(f"Failed to add {settings.DATABASE_TYPE.upper()}.{table_name} column {column_name}, operation error: {ex}")

    except Exception as ex:
        logging.critical(f"Failed to add {settings.DATABASE_TYPE.upper()}.{table_name} column {column_name}, error: {ex}")


def alter_db_column_type(migrator, table_name, column_name, new_column_type):
    try:
        migrate(migrator.alter_column_type(table_name, column_name, new_column_type))
    except Exception as ex:
        logging.critical(f"Failed to alter {settings.DATABASE_TYPE.upper()}.{table_name} column {column_name} type, error: {ex}")


def alter_db_rename_column(migrator, table_name, old_column_name, new_column_name):
    try:
        migrate(migrator.rename_column(table_name, old_column_name, new_column_name))
    except Exception:
        # rename fail will lead to a weired error.
        # logging.critical(f"Failed to rename {settings.DATABASE_TYPE.upper()}.{table_name} column {old_column_name} to {new_column_name}, error: {ex}")
        pass


def alter_db_drop_index(migrator, table_name, index_name):
    try:
        migrate(migrator.drop_index(table_name, index_name))
    except Exception:
        # rename fail will lead to a weired error.
        # logging.critical(f"Failed to rename {settings.DATABASE_TYPE.upper()}.{table_name} column {old_column_name} to {new_column_name}, error: {ex}")
        pass


def ensure_model_indexes(migrator):
    """Create indexes declared by the Peewee models when they are missing."""
    members = inspect.getmembers(sys.modules[__name__], inspect.isclass)
    for name, model in members:
        if model == DataBaseModel or not issubclass(model, DataBaseModel):
            continue

        table_name = model._meta.table_name
        expected = {}
        for field in model._meta.fields.values():
            if field.primary_key:
                continue
            if field.index or field.unique:
                expected[(field.name,)] = bool(field.unique)

        for columns, unique in model._meta.indexes:
            expected[tuple(columns)] = bool(unique)

        if not expected:
            continue

        try:
            existing = {tuple(index.columns): bool(index.unique) for index in DB.get_indexes(table_name)}
        except Exception as ex:
            logging.error(f"Failed to inspect indexes on {table_name}: {ex}")
            continue

        for columns, unique in expected.items():
            if columns in existing and (not unique or existing[columns]):
                continue
            try:
                migrate(migrator.add_index(table_name, columns, unique=unique))
                logging.info(f"Created {'unique ' if unique else ''}index on {table_name} ({', '.join(columns)})")
            except Exception as ex:
                logging.error(f"Failed to create {'unique ' if unique else ''}index on {table_name} ({', '.join(columns)}): {ex}")


def migrate_add_unique_email(migrator):
    """Deduplicates user emails and add UNIQUE constraint to email column (idempotent)"""
    # step 0: check existing index state on user.email and prepare for unique constraint
    try:
        cursor = DB.execute_sql("""
            SELECT index_name, non_unique
            FROM information_schema.statistics
            WHERE table_schema = DATABASE()
              AND table_name = 'user'
              AND column_name = 'email'
            LIMIT 1
        """)
        row = cursor.fetchone()
        if row:
            index_name, non_unique = row
            if non_unique == 0:
                logging.info("UNIQUE index on user.email already exists, skipping migration")
                return
            # Non-unique index exists (e.g. from old peewee index=True); drop it so
            # the upcoming ADD UNIQUE INDEX does not hit MySQL error 1061 "Duplicate key name".
            DB.execute_sql(f"ALTER TABLE `user` DROP INDEX `{index_name}`")
            logging.info(f"Dropped non-unique index '{index_name}' on user.email before adding unique index")
    except Exception as ex:
        logging.warning(f"Failed to check/prepare email index on user table: {ex}, continuing with migration")

    # step 1: rename duplicate rows so the UNIQUE constraint can be applied
    try:
        duplicates = User.select(User.email).group_by(User.email).having(fn.COUNT(User.id) > 1).tuples()
        for (dup_email,) in duplicates:
            # Keep the superuser row, or the oldest row if there is no superuser
            rows = list(User.select(User.id).where(User.email == dup_email).order_by(User.is_superuser.desc(), User.create_time.asc()).tuples())
            for (uid,) in rows[1:]:
                new_email = f"{dup_email}_DUPLICATE_{uid[:8]}"
                User.update(email=new_email).where(User.id == uid).execute()
                logging.warning("Renamed duplicate user %s email to %s during migration", uid, new_email)
    except Exception as ex:
        logging.critical("Failed to deduplicate user.email before adding UNIQUE constraint: %s", ex)
        return

    # step 2: add UNIQUE index via migrator
    try:
        migrate(migrator.add_index("user", ("email",), unique=True))
    except (OperationalError, ProgrammingError) as ex:
        msg = str(ex)
        already_exists = "1061" in msg or "Duplicate key name" in msg or "already exists" in msg.lower()
        if already_exists:
            pass
        else:
            logging.critical("Failed to add UNIQUE constraint on user.email: %s", ex)
    except Exception as ex:
        logging.critical("Failed to add UNIQUE constraint on user.email: %s", ex)


def update_tenant_llm_to_id_primary_key():
    """Add ID and set to primary key step by step."""
    _update_tenant_llm_to_id_primary_key_mysql()


def _update_tenant_llm_to_id_primary_key_mysql():
    """MySQL implementation: Add ID column and set as AUTO_INCREMENT primary key."""
    try:
        with DB.atomic():
            # 0. Check if 'id' column already exists
            cursor = DB.execute_sql("""
                            SELECT COLUMN_NAME
                            FROM INFORMATION_SCHEMA.COLUMNS
                            WHERE TABLE_SCHEMA = DATABASE()
                            AND TABLE_NAME = 'tenant_llm'
                            AND COLUMN_NAME = 'id'
                        """)
            if cursor.rowcount > 0:
                return

            # 1. Add nullable column
            DB.execute_sql("ALTER TABLE tenant_llm ADD COLUMN temp_id INT NULL")

            # 2. Set ID using MySQL user variables
            DB.execute_sql("SET @row = 0;")
            DB.execute_sql("UPDATE tenant_llm SET temp_id = (@row := @row + 1) ORDER BY tenant_id, llm_factory, llm_name;")

            # 3. Drop old primary key
            DB.execute_sql("ALTER TABLE tenant_llm DROP PRIMARY KEY")

            # 4. Update ID column to primary key with AUTO_INCREMENT
            DB.execute_sql("""
            ALTER TABLE tenant_llm
            MODIFY COLUMN temp_id INT NOT NULL AUTO_INCREMENT PRIMARY KEY
            """)

            # 5. Add unique key
            DB.execute_sql("""
                ALTER TABLE tenant_llm
                ADD CONSTRAINT uk_tenant_llm UNIQUE (tenant_id, llm_factory, llm_name)
            """)

            # 6. rename
            DB.execute_sql("ALTER TABLE tenant_llm RENAME COLUMN temp_id TO id")

            logging.info("Successfully updated tenant_llm to id primary key.")

    except Exception as e:
        logging.error(str(e))
        cursor = DB.execute_sql("""
                                    SELECT COLUMN_NAME
                                    FROM INFORMATION_SCHEMA.COLUMNS
                                    WHERE TABLE_SCHEMA = DATABASE()
                                    AND TABLE_NAME = 'tenant_llm'
                                    AND COLUMN_NAME = 'temp_id'
                                """)
        if cursor.rowcount > 0:
            DB.execute_sql("ALTER TABLE tenant_llm DROP COLUMN temp_id")


def backfill_docmind_search_cleanup_complete():
    """Record only active versions whose previous job and parser cleanup finished."""

    completed = (
        DocmindIngestionJob.select(DocmindIngestionJob.id)
        .join(
            DocmindSourceDocument,
            on=(DocmindIngestionJob.source_document_id == DocmindSourceDocument.id),
        )
        .switch(DocmindIngestionJob)
        .join(Document, on=(DocmindIngestionJob.document_id == Document.id))
        .switch(DocmindIngestionJob)
        .join(ParserRun, on=(DocmindIngestionJob.parser_run_id == ParserRun.id))
        .where(
            (DocmindIngestionJob.version_id == DocmindSourceVersion.id)
            & (DocmindIngestionJob.lifecycle_state == "COMPLETE")
            & (DocmindIngestionJob.cleanup_state == "COMPLETE")
            & (DocmindIngestionJob.host_cleanup_state == "COMPLETE")
            & (DocmindIngestionJob.project_id == DocmindSourceDocument.project_id)
            & (DocmindIngestionJob.source_id == DocmindSourceDocument.source_id)
            & (DocmindIngestionJob.document_id == DocmindSourceDocument.document_id)
            & (DocmindSourceDocument.deleted_at.is_null(True))
            & (DocmindSourceDocument.active_source_version_id == DocmindSourceVersion.id)
            & (DocmindSourceVersion.source_document_id == DocmindSourceDocument.id)
            & (DocmindSourceVersion.document_id == Document.id)
            & (DocmindIngestionJob.parser_run_id == DocmindSourceVersion.parser_run_id)
            & (DocmindIngestionJob.chunk_set_id == DocmindSourceVersion.chunk_set_id)
            & (ParserRun.doc_id == Document.id)
            & (ParserRun.chunk_set_id == DocmindSourceVersion.chunk_set_id)
            & (ParserRun.lifecycle.in_(("READY", "READY_WITH_WARNING")))
            & (ParserRun.raw_artifact_ref.is_null(True))
            & (ParserRun.staged_chunk_count > 0)
            & (Document.active_chunk_set_id == DocmindSourceVersion.chunk_set_id)
            & (Document.status == "1")
        )
    )
    return (
        DocmindSourceVersion.update(search_cleanup_complete=True)
        .where(
            (~DocmindSourceVersion.search_cleanup_complete)
            & (DocmindSourceVersion.lifecycle_state == "ACTIVE")
            & (fn.EXISTS(completed))
        )
        .execute()
    )


def migrate_db():
    logging.disable(logging.ERROR)
    migrator = DatabaseMigrator[settings.DATABASE_TYPE.upper()].value(DB)
    alter_db_add_column(migrator, "file", "source_type", CharField(max_length=128, null=False, default="", help_text="where dose this document come from", index=True))
    alter_db_add_column(migrator, "tenant", "rerank_id", CharField(max_length=128, null=False, default="BAAI/bge-reranker-v2-m3", help_text="default rerank model ID"))
    alter_db_add_column(migrator, "dialog", "rerank_id", CharField(max_length=128, null=False, default="", help_text="default rerank model ID"))
    alter_db_column_type(migrator, "dialog", "top_k", IntegerField(default=1024))
    alter_db_add_column(migrator, "tenant_llm", "api_key", CharField(max_length=2048, null=True, help_text="API KEY", index=True))
    alter_db_add_column(migrator, "api_token", "source", CharField(max_length=16, null=True, help_text="none|dialog", index=True))
    alter_db_add_column(migrator, "tenant", "tts_id", CharField(max_length=256, null=True, help_text="default tts model ID", index=True))
    alter_db_add_column(migrator, "api_4_conversation", "source", CharField(max_length=16, null=True, help_text="none|dialog", index=True))
    alter_db_add_column(migrator, "task", "retry_count", IntegerField(default=0))
    alter_db_column_type(migrator, "api_token", "dialog_id", CharField(max_length=32, null=True, index=True))
    alter_db_add_column(migrator, "tenant_llm", "max_tokens", IntegerField(default=8192, index=True))
    alter_db_add_column(migrator, "knowledgebase", "pagerank", IntegerField(default=0, index=False))
    alter_db_add_column(migrator, "api_token", "beta", CharField(max_length=255, null=True, index=True))
    alter_db_add_column(migrator, "task", "digest", TextField(null=True, help_text="task digest", default=""))
    alter_db_add_column(migrator, "task", "chunk_ids", LongTextField(null=True, help_text="chunk ids", default=""))
    alter_db_add_column(migrator, "task", "parse_run_id", CharField(max_length=32, null=True, help_text="parser-platform run identity", index=True))
    alter_db_add_column(migrator, "task", "chunk_set_id", CharField(max_length=32, null=True, help_text="run-scoped staging chunk set", index=True))
    alter_db_add_column(migrator, "conversation", "user_id", CharField(max_length=255, null=True, help_text="user_id", index=True))
    alter_db_add_column(migrator, "task", "task_type", CharField(max_length=32, null=False, default=""))
    alter_db_add_column(migrator, "task", "priority", IntegerField(default=0))
    alter_db_add_column(migrator, "llm", "is_tools", BooleanField(null=False, help_text="support tools", default=False))
    alter_db_rename_column(migrator, "task", "process_duation", "process_duration")
    alter_db_rename_column(migrator, "document", "process_duation", "process_duration")
    alter_db_add_column(migrator, "document", "suffix", CharField(max_length=32, null=False, default="", help_text="The real file extension suffix", index=True))
    alter_db_add_column(migrator, "dialog", "meta_data_filter", JSONField(null=True, default={}))
    alter_db_add_column(migrator, "dialog", "rerank_candidates_count", IntegerField(default=64))
    alter_db_add_column(migrator, "document", "active_chunk_set_id", CharField(max_length=32, null=True, help_text="currently searchable parser-platform chunk set", index=True))
    alter_db_add_column(migrator, "parser_run", "retained_from_lifecycle", CharField(max_length=32, null=True))
    alter_db_add_column(migrator, "parser_run", "chunking_config", JSONField(null=False, default=dict))
    alter_db_column_type(migrator, "tenant_llm", "api_key", TextField(null=True, help_text="API KEY"))
    alter_db_add_column(migrator, "tenant_llm", "status", CharField(max_length=1, null=False, help_text="is it validate(0: wasted, 1: validate)", default="1", index=True))
    alter_db_add_column(migrator, "llm_factories", "rank", IntegerField(default=0, index=False))
    alter_db_add_column(migrator, "api_4_conversation", "name", CharField(max_length=255, null=True, help_text="conversation name", index=False))
    alter_db_add_column(migrator, "api_4_conversation", "exp_user_id", CharField(max_length=255, null=True, help_text="exp_user_id", index=True))
    alter_db_column_type(migrator, "system_settings", "value", TextField(null=False, help_text="Configuration value (JSON, string, etc.)"))
    alter_db_add_column(migrator, "document", "content_hash", CharField(max_length=32, null=True, help_text="xxhash128 of document content for change detection", default="", index=True))
    alter_db_add_column(migrator, "document", "requested_parse_run_id", CharField(max_length=32, null=True, index=True))
    alter_db_column_type(migrator, "document", "size", BigIntegerField(default=0, index=True))
    alter_db_column_type(migrator, "file", "size", BigIntegerField(default=0, index=True))
    alter_db_add_column(migrator, "tenant", "ocr_id", CharField(max_length=128, null=True, help_text="default ocr model ID", index=True))
    alter_db_add_column(migrator, "tenant", "tenant_ocr_id", CharField(max_length=32, null=True, help_text="id in tenant_model", index=True))
    alter_db_add_column(
        migrator,
        "docmind_catalog_version",
        "readiness_mode",
        CharField(max_length=32, null=False, default="SEARCH_VALIDATED", index=True),
    )
    alter_db_add_column(
        migrator,
        "docmind_catalog_version",
        "source_ready_version_id",
        CharField(max_length=32, null=True, index=True),
    )
    alter_db_add_column(
        migrator,
        "docmind_catalog_version",
        "manual_saved_by",
        CharField(max_length=32, null=True, index=True),
    )
    alter_db_add_column(
        migrator,
        "docmind_catalog_version",
        "manual_saved_at",
        DateTimeField(null=True, index=True),
    )
    alter_db_add_column(
        migrator,
        "docmind_project",
        "source_root_file_id",
        CharField(max_length=32, null=True, index=True),
    )
    alter_db_add_column(
        migrator,
        "docmind_source",
        "default_folder_id",
        CharField(max_length=32, null=True, index=True),
    )
    alter_db_add_column(
        migrator,
        "docmind_ingestion_job",
        "retry_not_before",
        DateTimeField(null=True, index=True),
    )
    alter_db_add_column(migrator, "docmind_ingestion_job", "pdf_ocr_requested", BooleanField(default=False, null=False))
    alter_db_add_column(migrator, "docmind_source_document", "source_dirty", BooleanField(default=False, null=False))
    for table in ("docmind_source_document", "docmind_ingestion_job"):
        for column in ("observation_epoch", "observation_generation"):
            alter_db_add_column(migrator, table, column, BigIntegerField(default=0, null=False))
    alter_db_add_column(migrator, "docmind_source_document", "last_change_sequence", BigIntegerField(default=0, null=False))
    alter_db_add_column(migrator, "docmind_source_document", "last_change_received_at", DateTimeField(null=True, index=True))
    alter_db_add_column(migrator, "docmind_source_document", "stable_observed_at", DateTimeField(null=True))
    alter_db_add_column(migrator, "docmind_source_document", "latest_target_version_id", CharField(max_length=32, null=True))
    alter_db_add_column(migrator, "docmind_source_scan", "protocol_version", IntegerField(default=1, null=False))
    alter_db_add_column(migrator, "docmind_source_scan", "scan_start_epoch", BigIntegerField(default=0, null=False))
    alter_db_add_column(migrator, "docmind_source_scan", "scan_start_sequence", BigIntegerField(default=0, null=False))
    alter_db_add_column(migrator, "docmind_source_deletion", "deletion_generation", BigIntegerField(default=0, null=False))
    alter_db_add_column(migrator, "docmind_ingestion_job", "pdf_ocr_consented_by", CharField(max_length=32, null=True))
    alter_db_add_column(migrator, "docmind_ingestion_job", "pdf_ocr_consented_at", DateTimeField(null=True))
    alter_db_add_column(migrator, "docmind_ingestion_job", "pdf_ocr_consent_key_hash", CharField(max_length=64, null=True))
    alter_db_add_column(
        migrator,
        "docmind_source_version",
        "search_cleanup_complete",
        BooleanField(default=False, null=False),
    )
    backfill_docmind_search_cleanup_complete()
    alter_db_add_column(
        migrator,
        "docmind_source_document",
        "source_object_id",
        CharField(max_length=255, null=True, index=True),
    )
    alter_db_add_column(
        migrator,
        "docmind_source_document",
        "host_file_identity",
        CharField(max_length=255, null=True, index=True),
    )
    alter_db_add_column(
        migrator,
        "docmind_source_scan_entry",
        "host_file_identity",
        CharField(max_length=255, null=True, index=True),
    )
    alter_db_add_column(
        migrator,
        "docmind_folder",
        "source_file_id",
        CharField(max_length=32, null=True, index=True),
    )
    alter_db_add_column(
        migrator,
        "docmind_catalog_version",
        "snapshot_schema_version",
        IntegerField(default=1, null=False, index=True),
    )
    alter_db_add_column(
        migrator,
        "docmind_catalog_version",
        "source_tree_hash",
        CharField(max_length=64, null=True, index=True),
    )
    alter_db_add_column(
        migrator,
        "docmind_catalog_version",
        "root_identity_sha256",
        CharField(max_length=64, null=True, index=True),
    )
    alter_db_add_column(
        migrator,
        "docmind_folder_version",
        "parent_folder_id",
        CharField(max_length=32, null=True, index=True),
    )
    alter_db_add_column(
        migrator,
        "docmind_folder_version",
        "source_file_id",
        CharField(max_length=32, null=True, index=True),
    )
    alter_db_add_column(
        migrator,
        "docmind_folder_version",
        "relative_path",
        CharField(max_length=1024, null=True),
    )
    alter_db_add_column(
        migrator,
        "docmind_folder_version",
        "display_name",
        CharField(max_length=255, null=True),
    )
    alter_db_add_column(
        migrator,
        "docmind_folder_version",
        "ordinal",
        IntegerField(null=True),
    )
    alter_db_add_column(
        migrator,
        "docmind_folder_version",
        "depth",
        IntegerField(null=True),
    )
    alter_db_drop_index(migrator, "tenant_langfuse", "idx_tenant_langfuse_secret_key")
    alter_db_drop_index(migrator, "tenant_langfuse", "idx_tenant_langfuse_public_key")
    alter_db_drop_index(migrator, "tenant_langfuse", "idx_tenant_langfuse_host")
    # Drop both the explicit "idx_*" name from later migrations AND the
    # Peewee-auto-derived "<table-as-classname>_<col1>_<col2>" name from the
    # original TenantModelInstance definition (commit dc4b82523). Databases
    # created before #15460 dropped the model's `indexes = ((...,), True)`
    # tuple still carry the auto-named compound unique index, which makes a
    # second instance with an empty api_key (e.g. Ollama) fail with
    # "Duplicate entry ... for key 'tenantmodelinstance_api_key_provider_id'"
    # — see #15699.
    legacy_indexes = [
        ("tenant_model_instance", "idx_api_key_provider_id"),
        ("tenant_model_instance", "tenantmodelinstance_api_key_provider_id"),
        ("tenant_model", "idx_provider_model_instance"),
    ]
    for table_name, index_name in legacy_indexes:
        try:
            migrate(migrator.drop_index(table_name, index_name))
        except (OperationalError, ProgrammingError) as ex:
            msg = str(ex)
            can_skip = "1091" in msg or "can't DROP" in msg.lower() or "does not exist" in msg.lower() or "already exists" in msg.lower()
            if can_skip:
                pass
            else:
                logging.critical(f"Failed to drop index {index_name} on {table_name}: {ex}")
        except Exception as ex:
            logging.critical(f"Failed to drop index {index_name} on {table_name}: {ex}")
    logging.disable(logging.NOTSET)
    # this is after re-enabling logging to allow logging changed user emails
    migrate_add_unique_email(migrator)
    migrate_model_type_names()
    ensure_model_indexes(migrator)


def migrate_model_type_names():
    """Rename legacy model_type string values to the canonical asr/vision names.

    Previously the code used speech2text / image2text. LLMType now emits asr /
    vision, and the backend compares model_type strings directly. This idempotent
    data migration updates persisted rows in llm and tenant_llm before the new
    enum values are used at runtime.
    """
    RENAME_MAP = {
        "speech2text": "asr",
        "image2text": "vision",
    }
    tables = ["llm", "tenant_llm"]
    for table in tables:
        if not DB.table_exists(table):
            continue
        for old_name, new_name in RENAME_MAP.items():
            try:
                cursor = DB.execute_sql(
                    "UPDATE {} SET model_type = %s WHERE model_type = %s".format(table),
                    (new_name, old_name),
                )
                if cursor.rowcount:
                    logging.info(
                        "Migrated %s rows in %s.model_type from %s to %s",
                        cursor.rowcount,
                        table,
                        old_name,
                        new_name,
                    )
            except Exception as ex:
                logging.warning(
                    "Failed to migrate model_type values in %s (from %s to %s): %s",
                    table,
                    old_name,
                    new_name,
                    ex,
                )
