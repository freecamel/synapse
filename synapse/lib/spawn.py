import os
import asyncio
import logging
import threading
import contextlib
import collections
import multiprocessing

import synapse.exc as s_exc
import synapse.glob as s_glob
import synapse.common as s_common
import synapse.daemon as s_daemon
import synapse.telepath as s_telepath
import synapse.datamodel as s_datamodel

import synapse.lib.base as s_base
import synapse.lib.boss as s_boss
import synapse.lib.coro as s_coro
import synapse.lib.hive as s_hive
import synapse.lib.link as s_link
import synapse.lib.view as s_view

import synapse.lib.storm as s_storm
import synapse.lib.dyndeps as s_dyndeps
import synapse.lib.grammar as s_grammar

logger = logging.getLogger(__name__)

pid = os.getpid()

def corework(spawninfo, todo, done):

    # This logging call is okay to run since we're executing in
    # our own process space and no logging has been configured.
    s_common.setlogging(logger, spawninfo.get('loglevel'))

    async def workloop():

        s_glob.iAmLoop()

        async with await SpawnCore.anit(spawninfo) as core:

            async def storm(item):

                useriden = item.get('user')
                viewiden = item.get('view')

                storminfo = item.get('storm')

                opts = storminfo.get('opts')
                text = storminfo.get('query')

                user = core.auth.user(useriden)
                if user is None:
                    raise s_exc.NoSuchUser(iden=useriden)

                view = core.views.get(viewiden)
                if view is None:
                    raise s_exc.NoSuchView(iden=viewiden)

                async for mesg in view.streamstorm(text, opts=opts, user=user):
                    yield mesg

            while not core.isfini:

                item = await s_coro.executor(todo.get)
                if item is None:
                    return

                link = await s_link.fromspawn(item.get('link'))

                await s_daemon.t2call(link, storm, (item,), {})

                wasfini = link.isfini

                await link.fini()

                await s_coro.executor(done.put, wasfini)

    try:
        asyncio.run(workloop())
    except Exception as e:
        print(e)
        logger.exception('hmmmm?')
        raise
    except KeyboardInterrupt as e:
        print(e)
        logger.exception('sigint much?')
        raise

class SpawnProc(s_base.Base):
    '''
    '''
    async def __anit__(self, core):

        await s_base.Base.__anit__(self)

        self.core = core
        self.iden = s_common.guid()

        self.ready = asyncio.Event()
        self.mpctx = multiprocessing.get_context('spawn')

        self.todo = self.mpctx.Queue()
        self.done = self.mpctx.Queue()
        self.procstat = None

        self.obsolete = False

        spawninfo = await core.getSpawnInfo()

        def reaploop():
            '''
            Simply wait for the process to complete (run from a separate thread)
            '''
            print(f'{self}:{pid}: REAPLOOP JOINING PROC {self.proc}:{self.proc.pid} FROM {threading.current_thread()}')
            self.procstat = self.proc.join()
            print(f'{self}:{pid}: REAPLOOP JOINED PROC {self.proc}:{self.proc.pid} FROM {threading.current_thread()}')

        # avoid blocking the ioloop during process construction
        def getproc():
            self.proc = self.mpctx.Process(target=corework, args=(spawninfo, self.todo, self.done))
            self.proc.start()

        await s_coro.executor(getproc)

        s_coro.executor(reaploop)

        async def fini():
            print(f'{self}:{pid}: CALLING TERMINATE FOR {self.proc.pid}')
            self.proc.terminate()
            print(f'{self}:{pid}: CALLED TERMINATE {self.proc.pid}')
        self.onfini(fini)

    async def retire(self):
        logger.debug(f'Proc {self.proc.pid} marked obsolete')
        self.obsolete = True

    async def xact(self, mesg):
        def doit():
            thread = threading.current_thread()
            print(f'{self}:{pid}: DOIT CALLED FROM {thread}')
            self.todo.put(mesg)
            retn = self.done.get()
            print(f'{self}:{pid}: DOIT DONE [{retn}] FROM {thread}')
            return retn
        return await s_coro.executor(doit)

class SpawnPool(s_base.Base):

    async def __anit__(self, core):

        await s_base.Base.__anit__(self)

        self.core = core

        self.poolsize = await core.getConfOpt('spawn:poolsize')

        self.spawns = {}  # iden -> SpawnProc
        self.spawnq = collections.deque()  # sequence of available SpawnProc items.

        async def fini():
            await self.kill()

        self.onfini(fini)

    async def bump(self):
        [await s.retire() for s in list(self.spawns.values())]
        # procs = list(self.spawnq)
        # self.spawnq.clear()
        # [await s.fini() for s in procs]
        [await s.fini() for s in self.spawnq]
        self.spawnq.clear()

    async def kill(self):
        print(f'{self}:{pid}: START KILL FUNC')
        self.spawnq.clear()
        [await s.fini() for s in list(self.spawns.values())]
        print(f'{self}:{pid}: DONE WITH KILL FUNC')

    @contextlib.asynccontextmanager
    async def get(self):

        if self.isfini: # pragma: no cover
            raise s_exc.IsFini()

        proc = None

        if self.spawnq:
            proc = self.spawnq.popleft()

        if proc is None:
            proc = await self._new()

        yield proc

        await self._put(proc)

    async def _put(self, proc):

        if not proc.obsolete and len(self.spawnq) < self.poolsize:
            self.spawnq.append(proc)
            return

        await proc.fini()

    async def _new(self):

        proc = await SpawnProc.anit(self.core)

        logger.debug(f'New spawnProc with pid {proc.proc.pid}')

        self.spawns[proc.iden] = proc

        async def fini():
            self.spawns.pop(proc.iden, None)

        proc.onfini(fini)

        return proc

class SpawnCore(s_base.Base):

    async def __anit__(self, spawninfo):

        await s_base.Base.__anit__(self)

        self.pid = os.getpid()
        self.views = {}
        self.layers = {}
        self.spawninfo = spawninfo

        self.conf = spawninfo.get('conf')
        self.iden = spawninfo.get('iden')
        self.dirn = spawninfo.get('dirn')

        self.stormcmds = {}
        self.stormmods = spawninfo['storm']['mods']

        for name, ctor in spawninfo['storm']['cmds']['ctors']:
            self.stormcmds[name] = ctor

        for name, cdef in spawninfo['storm']['cmds']['cdefs']:

            def ctor(argv):
                return s_storm.PureCmd(cdef, argv)

            self.stormcmds[name] = ctor

        self.onfini(self._finfini)

        self.boss = await s_boss.Boss.anit()
        self.onfini(self.boss.fini)

        self.model = s_datamodel.Model()
        self.model.addDataModels(spawninfo.get('model'))

        self.prox = await s_telepath.openurl(f'cell://{self.dirn}')
        self.onfini(self.prox.fini)

        self.hive = await s_hive.openurl(f'cell://{self.dirn}', name='*/hive')
        async def hivefini():
            print('hive fini...')
            await self.hive.fini()
            print('hive finid!')
        self.onfini(hivefini)
        # self.onfini(self.hive.fini)

        # TODO cortex configured for remote auth...
        node = await self.hive.open(('auth',))
        self.auth = await s_hive.HiveAuth.anit(node)
        self.onfini(self.auth.fini)

        for layrinfo in self.spawninfo.get('layers'):

            iden = layrinfo.get('iden')
            path = ('cortex', 'layers', iden)

            ctorname = layrinfo.get('ctor')

            ctor = s_dyndeps.tryDynLocal(ctorname)

            node = await self.hive.open(path)
            layr = await ctor(self, node, readonly=True)

            self.onfini(layr)

            self.layers[iden] = layr

        for viewinfo in self.spawninfo.get('views'):

            iden = viewinfo.get('iden')
            path = ('cortex', 'views', iden)

            node = await self.hive.open(path)
            view = await s_view.View.anit(self, node)

            self.onfini(view)

            self.views[iden] = view

        # initialize pass-through methods from the telepath proxy
        self.runRuntLift = self.prox.runRuntLift

    def _finfini(self):
        print('fini in spawncore!')

    def getStormQuery(self, text):
        '''
        Parse storm query text and return a Query object.
        '''
        query = s_grammar.Parser(text).query()
        query.init(self)
        return query

    def _logStormQuery(self, text, user):
        '''
        Log a storm query.
        '''
        if self.conf.get('storm:log'):
            lvl = self.conf.get('storm:log:level')
            logger.log(lvl, 'Executing spawn storm query {%s} as [%s] from [%s]', text, user.name, self.pid)

    def getStormCmd(self, name):
        return self.stormcmds.get(name)

    async def getStormMods(self):
        return self.stormmods
