#!/usr/bin/env python3
#
# Copyright (C) 2018-2023 VyOS maintainers and contributors
#
# This program is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License version 2 or later as
# published by the Free Software Foundation.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program.  If not, see <http://www.gnu.org/licenses/>.

import os

from sys import exit

from vyos.config import Config
from vyos.configdict import dict_merge
from vyos.configdict import is_node_changed
from vyos.configverify import verify_vrf
from vyos.utils.process import call
from vyos.template import render
from vyos.xml import defaults
from vyos import ConfigError
from vyos import airbag
airbag.enable()

rsyslog_conf = '/etc/rsyslog.d/00-vyos.conf'
logrotate_conf = '/etc/logrotate.d/vyos-rsyslog'
systemd_override = r'/run/systemd/system/rsyslog.service.d/override.conf'

def get_config(config=None):
    if config:
        conf = config
    else:
        conf = Config()
    base = ['system', 'syslog']
    if not conf.exists(base):
        return None

    syslog = conf.get_config_dict(base, key_mangling=('-', '_'),
                                  get_first_key=True, no_tag_node_value_mangle=True)

    tmp = is_node_changed(conf, base + ['vrf'])
    if tmp: syslog.update({'restart_required': {}})

    # We have gathered the dict representation of the CLI, but there are default
    # options which we need to update into the dictionary retrived.
    default_values = defaults(base)
    # XXX: some syslog default values can not be merged here (originating from
    # a tagNode - remove and add them later per individual tagNode instance
    if 'console' in default_values:
        del default_values['console']
    for entity in ['global', 'user', 'host', 'file']:
        if entity in default_values:
            del default_values[entity]

    syslog = dict_merge(default_values, syslog)

    # XXX: add defaults for "console" tree
    if 'console' in syslog and 'facility' in syslog['console']:
        default_values = defaults(base + ['console', 'facility'])
        for facility in syslog['console']['facility']:
            syslog['console']['facility'][facility] = dict_merge(default_values,
                                                                syslog['console']['facility'][facility])

    # XXX: add defaults for "host" tree
    for syslog_type in ['host', 'user', 'file']:
        # Bail out early if there is nothing to do
        if syslog_type not in syslog:
            continue

        default_values_host = defaults(base + [syslog_type])
        if 'facility' in default_values_host:
            del default_values_host['facility']

        for tmp, tmp_config in syslog[syslog_type].items():
            syslog[syslog_type][tmp] = dict_merge(default_values_host, syslog[syslog_type][tmp])
            if 'facility' in tmp_config:
                default_values_facility = defaults(base + [syslog_type, 'facility'])
                for facility in tmp_config['facility']:
                    syslog[syslog_type][tmp]['facility'][facility] = dict_merge(default_values_facility,
                        syslog[syslog_type][tmp]['facility'][facility])

    return syslog

def verify(syslog):
    if not syslog:
        return None

    verify_vrf(syslog)

def generate(syslog):
    if not syslog:
        if os.path.exists(rsyslog_conf):
            os.unlink(rsyslog_conf)
        if os.path.exists(logrotate_conf):
            os.unlink(logrotate_conf)

        return None

    render(rsyslog_conf, 'rsyslog/rsyslog.conf.j2', syslog)
    render(systemd_override, 'rsyslog/override.conf.j2', syslog)
    render(logrotate_conf, 'rsyslog/logrotate.j2', syslog)

    # Reload systemd manager configuration
    call('systemctl daemon-reload')
    return None

def apply(syslog):
    systemd_socket = 'syslog.socket'
    systemd_service = 'syslog.service'
    if not syslog:
        call(f'systemctl stop {systemd_service} {systemd_socket}')
        return None

    # we need to restart the service if e.g. the VRF name changed
    systemd_action = 'reload-or-restart'
    if 'restart_required' in syslog:
        systemd_action = 'restart'

    call(f'systemctl {systemd_action} {systemd_service}')
    return None

if __name__ == '__main__':
    try:
        c = get_config()
        verify(c)
        generate(c)
        apply(c)
    except ConfigError as e:
        print(e)
        exit(1)
