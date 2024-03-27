import os
import pwd
import re
import subprocess
import smbclient
from datetime import datetime
from smbprotocol.exceptions import SMBAuthenticationError

from ..samba_util import SAMBA_WORKGROUP, SAMBA_DOMAIN, SAMBA_NETBIOS
from ..ldapconnector import LMNLdapReader as lr


def timestamp2date(t):
    return datetime.fromtimestamp(t).strftime("%d.%m.%Y %H:%M:%S")

def _get_recursive_dir_properties(path):
    """
    Get properties of folder, and call itself recursively for subfolders.

    :param path: Current SAMBA path to scan
    :type path: basestring
    :return: Files, subfolders, and their size, last modified date.
    :rtype: dict
    """

    properties = {
            'size':0,
            'last-modified': timestamp2date(smbclient.stat(path).st_mtime),
            'files': {},
            'dirs': {},
    }

    for item in smbclient.scandir(path):
        if item.is_file():
            stats = item.stat()
            size = stats.st_size
            lastmodified = timestamp2date(stats.st_mtime)
            properties['files'][item.name] = {
                'size': size,
                'last-modified': lastmodified,
            }

        elif item.is_dir():
            dir_path = f"{path}\\{item.name}"
            properties['dirs'][dir_path] = _get_recursive_dir_properties(dir_path)

    return properties

def samba_list_user_files(user):
    """
    Recursively list files and folder of a school root share, with size and last-modified properties.
    This function can only be called with a valid kerberos ticket.

    :param user: LDAP User
    :type user: basestring
    :return: All folders and files owned by user
    :rtype: dict
    """

    try:
        school = lr.getval(f'/users/{user}', 'sophomorixSchoolname')
        path = f'\\\\{SAMBA_NETBIOS}\\{school}'
        directories = {path: _get_recursive_dir_properties(path)}

        return directories
    except SMBAuthenticationError as e:
        print(f"Please check if you have a valid Kerberos Ticket for the user {user}.")
        return None

def list_user_files(user):
    path = '/srv/samba'
    user = f'{SAMBA_WORKGROUP}\\{user}'

    directories = {}

    for root, dirs, files in os.walk(path):
        for f in files:
            stats = os.stat(os.path.join(root, f))
            owner = pwd.getpwuid(stats.st_uid).pw_name
            size = stats.st_size
            if owner == user:
                for directory, _ in directories.items():
                    if root.startswith(directory):
                        directories[directory]['total'] += size
                        directories[directory]['files'][f] = f"{size / 1024 / 1024:.2f}"
                        break
                else:
                    directories[root] = {'total': size, 'files':{f: f"{size / 1024 / 1024:.2f}"}}

    total = 0
    for directory, details in directories.items():
        size = details['total']
        total += size
        mega = size / 1024 / 1024
        directories[directory]['total'] = f"{mega:.2f}"

    return {'directories': directories, 'total': f"{total / 1024 / 1024:.2f}"}

def get_user_quotas(user):
    # TODO: find a better way to get shares list
    sophomorixQuota = lr.getval(f'/users/{user}', 'sophomorixQuota')
    if sophomorixQuota is None:
        raise Exception(f'User {user} not found in ldap')

    quotas = {share.split(":")[0]: None for share in sophomorixQuota}

    with open('/etc/linuxmuster/.secret/administrator', 'r') as adm_pw:
        pw = adm_pw.readline().strip()

    def _format_quota(quota):
        if "NO LIMIT" in quota:
            return quota
        return round(int(quota) / 1024 /1024, 2)

    # TODO: parallel ?
    for share in quotas.keys():
        cmd = ['smbcquotas', '-U', f'administrator%{pw}', '-u', user, f'//{SAMBA_DOMAIN}/{share}']
        smbc_output = subprocess.run(cmd, capture_output=True)
        out, err = smbc_output.stdout.decode().strip(),  smbc_output.stderr.decode().strip()
        if 'cli_full_connection failed!' in err:
            # Catch Samba error
            error_code =  err.split("(")[1].strip(")")
            quotas[share] = {
                "used": error_code,
                "soft_limit": error_code,
                "hard_limit": error_code,
            }
        else:
            used, soft, hard = [value.strip("/") for value in re.split(r"\s{2,}", out)[2:]]
            quotas[share] = {
                "used": _format_quota(used),
                "soft_limit": _format_quota(soft),
                "hard_limit": _format_quota(hard),
            }

    pw = ''

    return quotas