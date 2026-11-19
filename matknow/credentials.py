import ctypes
import hashlib
import os
import sys
import tempfile
from ctypes import wintypes
from pathlib import Path

from matknow.model_config import DEFAULT_URL, completion_url

HEADER=b'MATKNOW-DPAPI\x01'
MAX_ENCRYPTED_BYTES=16384


class CredentialError(RuntimeError):
    pass


def validate_key(key: str) -> str:
    if not isinstance(key, str) or any(ord(c) < 32 or ord(c) > 126 for c in key):
        raise ValueError('API Key 只能包含可打印的 ASCII 字符，不能含换行或控制字符。')
    key = key.strip()
    if not key or len(key.encode('utf-8')) > 2560 or ' ' in key:
        raise ValueError('请输入有效的 API Key，长度不能超过 2560 字节。')
    return key


class _DataBlob(ctypes.Structure):
    _fields_=[('size',wintypes.DWORD),('data',ctypes.POINTER(wintypes.BYTE))]


def _crypt(data,decrypt=False):
    if sys.platform!='win32':raise CredentialError('API Key 加密保存需要 Windows DPAPI。')
    source=(wintypes.BYTE*len(data)).from_buffer_copy(data)
    incoming=_DataBlob(len(data),source);outgoing=_DataBlob()
    kernel=None
    try:
        crypto=ctypes.WinDLL('crypt32.dll',use_last_error=True,winmode=0x00000800)
        kernel=ctypes.WinDLL('kernel32.dll',use_last_error=True,winmode=0x00000800)
        kernel.LocalFree.argtypes=[ctypes.c_void_p];kernel.LocalFree.restype=ctypes.c_void_p
        function=crypto.CryptUnprotectData if decrypt else crypto.CryptProtectData
        function.argtypes=[ctypes.POINTER(_DataBlob),ctypes.c_void_p,ctypes.POINTER(_DataBlob),
                           ctypes.c_void_p,ctypes.c_void_p,wintypes.DWORD,ctypes.POINTER(_DataBlob)]
        function.restype=wintypes.BOOL
        # Current Windows user, no machine-wide flag or interactive prompt.
        if not function(ctypes.byref(incoming),None,None,None,None,1,ctypes.byref(outgoing)):
            raise OSError
        if not 0<outgoing.size<=MAX_ENCRYPTED_BYTES:raise OSError
        return ctypes.string_at(outgoing.data,outgoing.size)
    except OSError:
        raise CredentialError('无法解密此 API Key，请在当前电脑的模型设置中重新填写。' if decrypt else
                              'Windows 无法加密 API Key，未保存任何明文。') from None
    finally:
        ctypes.memset(source,0,len(data))
        if outgoing.data and kernel is not None:
            ctypes.memset(outgoing.data,0,outgoing.size);kernel.LocalFree(outgoing.data)


class CredentialStore:
    def __init__(self,root:Path,endpoint:str=DEFAULT_URL):
        self.root=Path(root).resolve()
        endpoint=completion_url(endpoint)
        if endpoint=='https://api.deepseek.com/v1/chat/completions':endpoint=completion_url(DEFAULT_URL)
        self.path=self.root/'credentials'/(hashlib.sha256(endpoint.encode('utf8')).hexdigest()+'.dpapi')
        self.target=os.path.normcase(str(self.path))

    def for_endpoint(self,endpoint):
        return CredentialStore(self.root,endpoint)

    def get(self):
        try:
            with self.path.open('rb') as file:data=file.read(MAX_ENCRYPTED_BYTES+1)
        except FileNotFoundError:return None
        except OSError:raise CredentialError('无法读取本项目的 API Key，请检查文件权限。') from None
        if not data.startswith(HEADER) or len(data)>MAX_ENCRYPTED_BYTES:
            raise CredentialError('本项目的加密 Key 文件无效，请重新填写 API Key。')
        try:return validate_key(_crypt(data[len(HEADER):],decrypt=True).decode('utf8'))
        except (ValueError,UnicodeError):
            raise CredentialError('本项目的加密 Key 文件无效，请重新填写 API Key。') from None

    def set(self,key):
        data=HEADER+_crypt(validate_key(key).encode('utf8'))
        temporary=None
        try:
            self.path.parent.mkdir(parents=True,exist_ok=True)
            with tempfile.NamedTemporaryFile(dir=self.path.parent,prefix='.key-',suffix='.tmp',delete=False) as file:
                temporary=Path(file.name);file.write(data);file.flush();os.fsync(file.fileno())
            os.replace(temporary,self.path)
        except OSError:raise CredentialError('无法保存本项目的加密 Key，请检查目录权限。') from None
        finally:
            if temporary is not None:
                try:temporary.unlink(missing_ok=True)
                except OSError:pass

    def delete(self):
        try:self.path.unlink(missing_ok=True)
        except OSError:raise CredentialError('无法删除本项目的加密 Key，请检查文件权限。') from None
