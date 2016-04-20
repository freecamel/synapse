import os
import sys
import json
import logging
import argparse
import subprocess

import synapse.daemon as s_daemon

import synapse.lib.cli as s_cli
import synapse.lib.thishost as s_thishost

# TODO management CLI for individual dmon *and* svcbus!
# FIXME how to implement unit tests for the onboot/noboot helpers?

logger = logging.getLogger(__name__)

LOG_LEVEL_CHOICES = ('debug', 'info', 'warning', 'error', 'critical')

# FIXME CONFIG FILE DOCS

def getArgParser():
    p = s_cli.ArgumentParser()
    p.add_argument('--listen', nargs='+', default=[], help='add a synapse link listener url')
    p.add_argument('--onboot', default=False, action='store_true',help='Configure the dmon for startup on reboot and add configs')
    p.add_argument('--noboot', default=False, action='store_true',help='Remove a dmon config from the onboot list')
    p.add_argument('--log-level', choices=LOG_LEVEL_CHOICES, help='specify the log level')
    p.add_argument('--run-svcbus', default=False, action='store_true', help='run and share a ServiceBus at /svcbus')

    p.add_argument('configs', nargs='*', help='json config file(s)')

    return p

homedir = os.path.expanduser('~')
dmondir = os.path.join(homedir,'.syn.dmon')
cfgfile = os.path.join(dmondir,'onboot.json')
onefile = os.path.join(dmondir,'onboot.once')

def initconf():
    '''
    Load ( create if needed ) the dmon onboot json config.

    ( ~/.syn.dmon/onboot.json )

    Example:

        conf = initconf()

        # ... modify stuff ...

        saveconf(conf)

    '''
    os.makedirs(dmondir,mode=0o700,exist_ok=True)

    if not os.path.isfile(onefile):
        initboot()

    if not os.path.isfile(cfgfile):
        conf = {'includes':[]}
        with open(cfgfile,'wb') as fd:
            fd.write( json.dumps(conf).encode('utf8') )
        return conf

    with open(cfgfile,'rb') as fd:
        return json.loads( fd.read().decode('utf8') )

def saveconf(conf):
    '''
    Save the config dict to the onboot json config.
    '''
    with open(cfgfile,'wb') as fd:
        fd.write( json.dumps(conf).encode('utf8') )

cronbloc = '''
@reboot "%s" -m synapse.tools.dmon "%s" &
''' % (sys.executable,cfgfile)

def initboot():
    '''
    Initialize an @reboot config to fire a dmon.
    '''
    try:
        crontext = subprocess.check_output(['crontab','-l'])
    except Exception as e:
        crontext = b''

    proc = subprocess.Popen(['crontab','-'], stdin=subprocess.PIPE)
    proc.stdin.write( crontext + cronbloc.encode('utf8') )
    proc.stdin.close()

    proc.wait()

    with open(onefile,'wb') as fd:
        fd.write(b'once')

def onboot(path):
    '''
    Install a config by confirming a cron @reboot dmon config and
    addin the given path to the includes for the startup config.

    Note: The original file path will be read in
    '''
    path = os.path.abspath(path)
    # ensure the file is valid json
    with open(path,'rb') as fd:
        json.loads( fd.read().decode('utf8') )

    conf = initconf()

    incs = conf.get('includes')
    if path in incs:
        return False

    incs.append(path)

    saveconf(conf)
    return True

def noboot(path):
    '''
    Remove a config from the list of onboot dmon configs.
    '''
    path = os.path.abspath(path)

    conf = initconf()
    incs = conf.get('includes')
    if path in incs:
        incs.remove(path)
    saveconf(conf)

def main(argv):

    p = getArgParser()
    opts = p.parse_args(argv)

    if opts.log_level:
        logging.basicConfig(level=opts.log_level.upper())
        logger.info('log level set to ' + opts.log_level)

    if opts.onboot:
        plat = s_thishost.get('platform')
        if plat not in ('linux','darwin'):
            raise Exception('--onboot does not support platform: %s' % (plat,))

        for path in opts.configs:
            logger.info('onboot add: %s' % (path,))
            onboot(path)

        return

    if opts.noboot:
        for path in opts.configs:
            logger.info('onboot del: %s' % (path,))
            noboot(path)
        return

    dmon = s_daemon.Daemon()

    for url in opts.listen:
        dmon.listen(url)

    if opts.run_svcbus:
        svcbus = s_service.SvcBus()
        dmon.share('svcbus',svcbus)

    for path in opts.configs:
        dmon.loadDmonFile(path)

    dmon.main()

if __name__ == '__main__':
    sys.exit( main( sys.argv[1:] ) )