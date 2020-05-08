import json

import synapse.exc as s_exc
import synapse.common as s_common

import synapse.lib.ast as s_ast

import synapse.tests.utils as s_test

foo_stormpkg = {
    'name': 'foo',
    'desc': 'The Foo Module',
    'version': (0, 0, 1),
    'modules': [
        {
            'name': 'hehe.haha',
            'storm': '''
                $intval = $(10)
                function lolz(x, y) {
                    return ($( $x + $y ))
                }
            ''',
        },
        {
            'name': 'hehe.hoho',
            'storm': '''
                function nodes (x, y) {
                    [ test:str=$x ]
                    [ test:str=$y ]
                }
            ''',
        },
        {
            'name': 'test',
            'storm': '''
            function pprint(arg1, arg2, arg3) {
                $lib.print('arg1: {arg1}', arg1=$arg1)
                $lib.print('arg2: {arg2}', arg2=$arg2)
                $lib.print('arg3: {arg3}', arg3=$arg3)
                return()
            }
            '''
        },
        {
            'name': 'importnest',
            'storm': '''
            $counter = 0
            $foobar = 0

            function inner(arg2, add) {
                $foobar = $( $foobar + $add )
                $lib.print('counter is {c}', c=$counter)
                if $( $arg2 ) {
                    $retn = "foo"
                } else {
                    $retn = "bar"
                }
                return ($retn)
            }

            function outer(arg1, add) {
                $strbase = $lib.str.format("(Run: {c}) we got back ", c=$counter)
                $reti = $inner($arg1, $add)
                $mesg = $lib.str.concat($strbase, $reti)
                $counter = $( $counter + $add )
                $lib.print("foobar is {foobar}", foobar=$foobar)
                return ($mesg)
            }
            ''',
        },
        {
            'name': 'yieldsforever',
            'storm': '''
            $splat = 18
            function rockbottom(arg1) {
                [test:str = $arg1]
            }

            function middlechild(arg2) {
                yield $rockbottom($arg2)
            }

            function yieldme(arg3) {
                yield $middlechild($arg3)
            }
            ''',
        },
    ],
    'commands': [
        {
            'name': 'foo.bar',
            'storm': '''
                init {
                    $foolib = $lib.import(hehe.haha)
                    [ test:int=$foolib.lolz($(20), $(30)) ]
                }
            ''',
        },
        {
            'name': 'test.nodes',
            'storm': '''
                $foolib = $lib.import(hehe.hoho)
                yield $foolib.nodes(asdf, qwer)
            ''',
        },
    ],
}

class AstTest(s_test.SynTest):

    async def test_try_set(self):
        '''
        Test ?= assignment
        '''
        async with self.getTestCore() as core:

            nodes = await core.nodes('[ test:str?=(1,2,3,4) ]')
            self.len(0, nodes)
            nodes = await core.nodes('[test:int?=4] [ test:int?=nonono ]')
            self.len(1, nodes)
            nodes = await core.nodes('[test:comp?=(yoh,nope)]')
            self.len(0, nodes)

            nodes = await core.nodes('[test:str=foo :hehe=no42] [test:int?=:hehe]')
            self.len(1, nodes)

            nodes = await core.nodes('[ test:str=foo :tick?=2019 ]')
            self.len(1, nodes)
            self.eq(nodes[0].get('tick'), 1546300800000)
            nodes = await core.nodes('[ test:str=foo :tick?=notatime ]')
            self.len(1, nodes)
            self.eq(nodes[0].get('tick'), 1546300800000)

    async def test_ast_subq_vars(self):

        async with self.getTestCore() as core:

            # Show a runtime variable being smashed by a subquery
            # variable assignment
            q = '''
                $loc=newp
                [ test:comp=(10, lulz) ]
                { -> test:int [ :loc=haha ] $loc=:loc }
                $lib.print($loc)
            '''
            msgs = await core.stormlist(q)
            self.stormIsInPrint('haha', msgs)

            # Show that a computed variable being smashed by a
            # subquery variable assignment with multiple nodes
            # traveling through a subquery.
            async with await core.snap() as snap:
                await snap.addNode('test:comp', (30, 'w00t'))
                await snap.addNode('test:comp', (40, 'w00t'))
                await snap.addNode('test:int', 30, {'loc': 'sol'})
                await snap.addNode('test:int', 40, {'loc': 'mars'})

            q = '''
                test:comp:haha=w00t
                { -> test:int $loc=:loc }
                $lib.print($loc)
                -test:comp
            '''
            msgs = await core.stormlist(q)
            self.stormIsInPrint('sol', msgs)
            self.stormIsInPrint('mars', msgs)

    async def test_ast_runtsafe_bug(self):
        '''
        A regression test where the runtsafety of $newvar was incorrect
        '''
        async with self.getTestCore() as core:
            q = '''
                [test:str=another :hehe=asdf]
                $s = $lib.text("Foo")
                $newvar=:hehe
                -.created
                $s.add("yar {x}", x=$newvar)
                $lib.print($s.str())
            '''
            mesgs = await core.stormlist(q)
            prints = [m[1]['mesg'] for m in mesgs if m[0] == 'print']
            self.eq(['Foo'], prints)

    async def test_ast_variable_props(self):
        async with self.getTestCore() as core:
            # editpropset
            q = '$var=hehe [test:str=foo :$var=heval]'
            nodes = await core.nodes(q)
            self.len(1, nodes)
            self.eq('heval', nodes[0].get('hehe'))

            # filter
            q = '[test:str=heval] test:str $var=hehe +:$var'
            nodes = await core.nodes(q)
            self.len(1, nodes)
            self.eq('heval', nodes[0].get('hehe'))

            # prop del
            q = '[test:str=foo :tick=2019] $var=tick [-:$var]'
            nodes = await core.nodes(q)
            self.len(1, nodes)
            self.none(nodes[0].get('tick'))

            # pivot
            q = 'test:str=foo $var=hehe :$var -> test:str'
            nodes = await core.nodes(q)
            self.len(1, nodes)
            self.eq('heval', nodes[0].ndef[1])

            q = '[test:pivcomp=(xxx,foo)] $var=lulz :$var -> *'
            nodes = await core.nodes(q)
            self.len(1, nodes)
            self.eq('foo', nodes[0].ndef[1])

            # univ set
            q = 'test:str=foo $var=seen [.$var=2019]'
            nodes = await core.nodes(q)
            self.len(1, nodes)
            self.nn(nodes[0].get('.seen'))

            # univ filter (no var)
            q = 'test:str -.created'
            nodes = await core.nodes(q)
            self.len(0, nodes)

            # univ filter (var)
            q = 'test:str $var="seen" +.$var'
            nodes = await core.nodes(q)
            self.len(1, nodes)
            self.nn(nodes[0].get('.seen'))

            # univ delete
            q = 'test:str=foo $var="seen" [ -.$var ] | spin | test:str=foo'
            nodes = await core.nodes(q)
            self.len(1, nodes)
            self.none(nodes[0].get('.seen'))

            # Sad paths
            q = '[test:str=newp -.newp]'
            await self.asyncraises(s_exc.NoSuchProp, core.nodes(q))
            q = '$newp=newp [test:str=newp -.$newp]'
            await self.asyncraises(s_exc.NoSuchProp, core.nodes(q))

    async def test_ast_editparens(self):

        async with self.getTestCore() as core:

            q = '[(test:str=foo)]'
            nodes = await core.nodes(q)
            self.len(1, nodes)

            q = '$val=zoo test:str=foo [(test:str=bar test:str=baz :hehe=$val)]'
            nodes = await core.nodes(q)
            self.len(3, nodes)

            # :hehe doesn't get applied to nodes incoming to editparens
            self.none(nodes[0].get('hehe'))
            self.eq('zoo', nodes[1].get('hehe'))
            self.eq('zoo', nodes[2].get('hehe'))

            with self.raises(s_exc.NoSuchForm):
                await core.nodes('[ (newp:newp=20 :hehe=10) ]')

            # Test for nonsensicalness
            q = 'test:str=baz [(test:str=:hehe +#visi)]'
            nodes = await core.nodes(q)

            self.eq(('test:str', 'baz'), nodes[0].ndef)
            self.eq(('test:str', 'zoo'), nodes[1].ndef)

            self.nn(nodes[1].tags.get('visi'))
            self.none(nodes[0].tags.get('visi'))

            nodes = await core.nodes('[ inet:ipv4=1.2.3.4 ]  [ (inet:dns:a=(vertex.link, $node.value()) +#foo ) ]')
            self.eq(nodes[0].ndef, ('inet:ipv4', 0x01020304))
            self.none(nodes[0].tags.get('foo'))
            self.eq(nodes[1].ndef, ('inet:dns:a', ('vertex.link', 0x01020304)))
            self.nn(nodes[1].tags.get('foo'))

            # test nested
            nodes = await core.nodes('[ inet:fqdn=woot.com ( ps:person="*" :name=visi (ps:contact="*" +#foo )) ]')
            self.eq(nodes[0].ndef, ('inet:fqdn', 'woot.com'))

            self.eq(nodes[1].ndef[0], 'ps:person')
            self.eq(nodes[1].props.get('name'), 'visi')
            self.none(nodes[1].tags.get('foo'))

            self.eq(nodes[2].ndef[0], 'ps:contact')
            self.nn(nodes[2].tags.get('foo'))

            user = await core.auth.addUser('newb')
            with self.raises(s_exc.AuthDeny):
                await core.nodes('[ (inet:ipv4=1.2.3.4 :asn=20) ]', opts={'user': user.iden})

    async def test_subquery_yield(self):

        async with self.getTestCore() as core:
            q = '[test:comp=(10,bar)] { -> test:int}'
            nodes = await core.nodes(q)
            self.len(1, nodes)
            self.eq('test:comp', nodes[0].ndef[0])

            q = '[test:comp=(10,bar)] yield { -> test:int}'
            nodes = await core.nodes(q)
            self.len(2, nodes)
            kinds = [nodes[0].ndef[0], nodes[1].ndef[0]]
            self.sorteq(kinds, ['test:comp', 'test:int'])

    async def test_ast_var_in_tags(self):
        async with self.getTestCore() as core:
            q = '[test:str=foo +#base.tag1=(2014,?)]'
            await core.nodes(q)

            q = '$var=tag1 #base.$var'
            nodes = await core.nodes(q)
            self.len(1, nodes)

            q = '$var=not #base.$var'
            nodes = await core.nodes(q)
            self.len(0, nodes)

            q = 'test:str $var=tag1 +#base.$var'
            nodes = await core.nodes(q)
            self.len(1, nodes)

            q = 'test:str $var=tag1 +#base.$var@=2014'
            nodes = await core.nodes(q)
            self.len(1, nodes)

            q = 'test:str $var=tag1 -> #base.$var'
            nodes = await core.nodes(q)
            self.len(1, nodes)

            q = 'test:str $var=nope -> #base.$var'
            nodes = await core.nodes(q)
            self.len(0, nodes)

            q = 'test:str [+#base.tag1.foo] $var=tag1 -> #base.$var.*'
            nodes = await core.nodes(q)
            self.len(1, nodes)

            q = 'test:str $var=tag2 [+#base.$var]'
            nodes = await core.nodes(q)
            self.len(1, nodes)
            self.sorteq(nodes[0].tags, ('base', 'base.tag1', 'base.tag1.foo', 'base.tag2'))

    async def test_ast_var_in_deref(self):

        async with self.getTestCore() as core:

            q = '''
            $d = $lib.dict(foo=bar, bar=baz, baz=biz)
            for ($key, $val) in $d {
                [ test:str=$d.$key ]
            }
            '''
            nodes = await core.nodes(q)
            self.len(3, nodes)
            reprs = set(map(lambda n: n.repr(), nodes))
            self.eq(set(['bar', 'baz', 'biz']), reprs)

            q = '''
            $data = $lib.dict(foo=$lib.dict(bar=$lib.dict(woot=final)))
            $varkey=woot
            [ test:str=$data.foo.bar.$varkey ]
            '''
            nodes = await core.nodes(q)
            self.len(1, nodes)
            self.eq('final', nodes[0].repr())

            q = '''
            $bar = bar
            $car = car

            $f = var
            $g = tar
            $de = $lib.dict(car=$f, zar=$g)
            $dd = $lib.dict(mar=$de)
            $dc = $lib.dict(bar=$dd)
            $db = $lib.dict(var=$dc)
            $foo = $lib.dict(woot=$db)
            [ test:str=$foo.woot.var.$bar.mar.$car ]
            '''
            nodes = await core.nodes(q)
            self.len(1, nodes)
            self.eq('var', nodes[0].repr())

            q = '''
            $data = $lib.dict('vertex project'=foobar)
            $"spaced key" = 'vertex project'
            [ test:str = $data.$"spaced key" ]
            '''
            nodes = await core.nodes(q)
            self.len(1, nodes)
            self.eq('foobar', nodes[0].repr())

            q = '''
            $data = $lib.dict('bar baz'=woot)
            $'new key' = 'bar baz'
            [ test:str=$data.$'new key' ]
            '''
            nodes = await core.nodes(q)
            self.len(1, nodes)
            self.eq('woot', nodes[0].repr())

            q = '''
            $bottom = $lib.dict(lastkey=synapse)
            $subdata = $lib.dict('bar baz'=$bottom)
            $data = $lib.dict(vertex=$subdata)
            $'new key' = 'bar baz'
            $'over key' = vertex
            [ test:str=$data.$'over key'.$"new key".lastkey ]
            '''
            nodes = await core.nodes(q)
            self.len(1, nodes)
            self.eq('synapse', nodes[0].repr())

            q = '''
            $data = $lib.dict(foo=bar)
            $key = nope
            [ test:str=$data.$key ]
            '''
            mesgs = await core.stormlist(q)
            errs = [m[1] for m in mesgs if m[0] == 'err']
            self.eq(errs[0][0], 'BadTypeValu')

    async def test_ast_array_pivot(self):

        async with self.getTestCore() as core:

            nodes = await core.nodes('[ test:arrayprop="*" :ints=(1, 2, 3) ]')
            self.len(1, nodes)

            # Check that subs were added
            nodes = await core.nodes('test:int=1')
            self.len(1, nodes)
            nodes = await core.nodes('test:int=2')
            self.len(1, nodes)
            nodes = await core.nodes('test:int=3')
            self.len(1, nodes)

            nodes = await core.nodes('test:arrayprop -> *')
            self.len(3, nodes)

            nodes = await core.nodes('test:arrayprop -> test:int')
            self.len(3, nodes)

            nodes = await core.nodes('test:arrayprop:ints -> test:int')
            self.len(3, nodes)

            nodes = await core.nodes('test:arrayprop:ints -> *')
            self.len(3, nodes)

            nodes = await core.nodes('test:arrayprop :ints -> *')
            self.len(3, nodes)

            nodes = await core.nodes('test:int=1 <- * +test:arrayprop')
            self.len(1, nodes)

            nodes = await core.nodes('test:int=2 -> test:arrayprop')
            self.len(1, nodes)
            self.eq(nodes[0].ndef[0], 'test:arrayprop')

    async def test_ast_pivot_ndef(self):

        async with self.getTestCore() as core:
            nodes = await core.nodes('[ edge:refs=((test:int, 10), (test:str, woot)) ]')
            nodes = await core.nodes('edge:refs -> test:str')
            self.eq(nodes[0].ndef, ('test:str', 'woot'))

            nodes = await core.nodes('[ geo:nloc=((inet:fqdn, woot.com), "34.1,-118.3", now) ]')
            self.len(1, nodes)

            # test a reverse ndef pivot
            nodes = await core.nodes('inet:fqdn=woot.com -> geo:nloc')
            self.len(1, nodes)
            self.eq('geo:nloc', nodes[0].ndef[0])

    async def test_ast_lift_filt_array(self):

        async with self.getTestCore() as core:

            with self.raises(s_exc.NoSuchCmpr):
                await core.nodes('test:arrayprop:ints*[^=asdf]')

            with self.raises(s_exc.BadTypeDef):
                await core.addFormProp('test:int', '_hehe', ('array', {'type': None}), {})

            with self.raises(s_exc.BadPropDef):
                await core.addTagProp('array', ('array', {'type': 'int'}), {})

            await core.addFormProp('test:int', '_hehe', ('array', {'type': 'int'}), {})
            nodes = await core.nodes('[ test:int=9999 :_hehe=(1, 2, 3) ]')
            self.len(1, nodes)
            nodes = await core.nodes('test:int=9999 :_hehe -> *')
            self.len(0, nodes)
            await core.nodes('test:int=9999 | delnode')
            await core.delFormProp('test:int', '_hehe')

            with self.raises(s_exc.NoSuchProp):
                await core.nodes('test:arrayprop:newp*[^=asdf]')

            with self.raises(s_exc.BadTypeValu):
                await core.nodes('test:comp:hehe*[^=asdf]')

            await core.nodes('[ test:comp=(10,asdf) ]')

            with self.raises(s_exc.BadCmprType):
                await core.nodes('test:comp +:hehe*[^=asdf]')

            nodes = await core.nodes('[ test:arrayprop="*" :ints=(1, 2, 3) ]')
            nodes = await core.nodes('[ test:arrayprop="*" :ints=(100, 101, 102) ]')

            with self.raises(s_exc.NoSuchProp):
                await core.nodes('test:arrayprop +:newp*[^=asdf]')

            nodes = await core.nodes('test:arrayprop:ints*[=3]')
            self.len(1, nodes)
            self.eq(nodes[0].repr('ints'), ('1', '2', '3'))

            nodes = await core.nodes('test:arrayprop:ints*[ range=(50,100) ]')
            self.len(1, nodes)
            self.eq(nodes[0].get('ints'), (100, 101, 102))

            nodes = await core.nodes('test:arrayprop +:ints*[ range=(50,100) ]')
            self.len(1, nodes)
            self.eq(nodes[0].get('ints'), (100, 101, 102))

            nodes = await core.nodes('test:arrayprop:ints=(1, 2, 3) | limit 1 | [ -:ints ]')
            self.len(1, nodes)

            # test filter case where field is None
            nodes = await core.nodes('test:arrayprop +:ints*[=100]')
            self.len(1, nodes)
            self.eq(nodes[0].get('ints'), (100, 101, 102))

    async def test_ast_array_addsub(self):

        async with self.getTestCore() as core:

            guid = s_common.guid()
            nodes = await core.nodes(f'[ test:arrayprop={guid} ]')

            # test starting with the property unset
            nodes = await core.nodes(f'test:arrayprop={guid} [ :ints+=99 ]')
            self.eq((99,), nodes[0].get('ints'))

            # test that removing a non-existant item is ok...
            nodes = await core.nodes(f'test:arrayprop={guid} [ :ints-=22 ]')

            nodes = await core.nodes(f'test:arrayprop={guid} [ :ints-=99 ]')
            self.eq((), nodes[0].get('ints'))

            nodes = await core.nodes(f'test:arrayprop={guid} [ :ints=(1, 2, 3) ]')

            nodes = await core.nodes(f'test:arrayprop={guid} [ :ints+=4 ]')
            self.eq((1, 2, 3, 4), nodes[0].get('ints'))

            nodes = await core.nodes(f'test:arrayprop={guid} [ :ints-=3 ]')
            self.eq((1, 2, 4), nodes[0].get('ints'))

            with self.raises(s_exc.BadTypeValu):
                await core.nodes(f'test:arrayprop={guid} [ :ints+=asdf ]')

            with self.raises(s_exc.BadTypeValu):
                await core.nodes(f'test:arrayprop={guid} [ :ints-=asdf ]')

            await core.nodes(f'test:arrayprop={guid} [ :ints?-=asdf ]')
            self.eq((1, 2, 4), nodes[0].get('ints'))

            await core.nodes(f'test:arrayprop={guid} [ :ints?+=asdf ]')
            self.eq((1, 2, 4), nodes[0].get('ints'))

            # ensure that we get a proper exception when using += (et al) on non-array props
            with self.raises(s_exc.StormRuntimeError):
                nodes = await core.nodes(f'[ inet:ipv4=1.2.3.4 :asn+=10 ]')

            with self.raises(s_exc.StormRuntimeError):
                nodes = await core.nodes(f'[ inet:ipv4=1.2.3.4 :asn?+=10 ]')

            with self.raises(s_exc.StormRuntimeError):
                nodes = await core.nodes(f'[ inet:ipv4=1.2.3.4 :asn-=10 ]')

            with self.raises(s_exc.StormRuntimeError):
                nodes = await core.nodes(f'[ inet:ipv4=1.2.3.4 :asn?-=10 ]')

    async def test_ast_del_array(self):

        async with self.getTestCore() as core:

            nodes = await core.nodes('[ test:arrayprop="*" :ints=(1, 2, 3) ]')
            nodes = await core.nodes('test:arrayprop [ -:ints ]')

            self.len(1, nodes)
            self.none(nodes[0].get('ints'))

            nodes = await core.nodes('test:int=2 -> test:arrayprop')
            self.len(0, nodes)

            nodes = await core.nodes('test:arrayprop:ints=(1, 2, 3)')
            self.len(0, nodes)

    async def test_ast_univ_array(self):
        async with self.getTestCore() as core:
            nodes = await core.nodes('[ test:int=10 .univarray=(1, 2, 3) ]')
            self.len(1, nodes)
            self.eq(nodes[0].get('.univarray'), (1, 2, 3))

            nodes = await core.nodes('.univarray*[=2]')
            self.len(1, nodes)

            nodes = await core.nodes('test:int=10 [ .univarray=(1, 3) ]')
            self.len(1, nodes)

            nodes = await core.nodes('.univarray*[=2]')
            self.len(0, nodes)

            nodes = await core.nodes('test:int=10 [ -.univarray ]')
            self.len(1, nodes)

            nodes = await core.nodes('.univarray')
            self.len(0, nodes)

    async def test_ast_embed_compute(self):
        # currently a simple smoke test for the EmbedQuery.compute method
        async with self.getTestCore() as core:
            nodes = await core.nodes('[ test:int=10 test:int=20 ]  $q=${#foo.bar}')
            self.len(2, nodes)

    async def test_lib_ast_module(self):

        otherpkg = {
            'name': 'foosball',
            'version': (0, 0, 1),
        }

        stormpkg = {
            'name': 'stormpkg',
            'version': (1, 2, 3)
        }

        async with self.getTestCore() as core:

            await core.addStormPkg(foo_stormpkg)

            nodes = await core.nodes('foo.bar')

            self.len(1, nodes)
            self.eq(nodes[0].ndef, ('test:int', 50))

            nodes = await core.nodes('test.nodes')
            self.len(2, nodes)
            self.eq({('test:str', 'asdf'), ('test:str', 'qwer')},
                    {n.ndef for n in nodes})

            msgs = await core.stormlist('pkg.list')
            self.stormIsInPrint('foo                             : (0, 0, 1)', msgs)

            msgs = await core.stormlist('pkg.del asdf')
            self.stormIsInPrint('No package names match "asdf". Aborting.', msgs)

            await core.addStormPkg(otherpkg)
            msgs = await core.stormlist('pkg.list')
            self.stormIsInPrint('foosball', msgs)

            msgs = await core.stormlist('pkg.del foo')
            self.stormIsInPrint('Multiple package names match "foo". Aborting.', msgs)

            msgs = await core.stormlist(f'pkg.del foosball')
            self.stormIsInPrint('Removing package: foosball', msgs)

            msgs = await core.stormlist(f'pkg.del foo')
            self.stormIsInPrint('Removing package: foo', msgs)

            # Direct add via stormtypes
            await core.stormlist('$lib.pkg.add($pkg)',
                                   opts={'vars': {'pkg': stormpkg}})
            msgs = await core.stormlist('pkg.list')
            self.stormIsInPrint('stormpkg', msgs)

            with self.raises(s_exc.NoSuchName):
                nodes = await core.nodes('test.nodes')

    async def test_function(self):
        async with self.getTestCore() as core:
            await core.addStormPkg(foo_stormpkg)

            # No arguments
            q = '''
            function hello() {
                return ("hello")
            }
            $retn=$hello()
            $lib.print('retn is: {retn}', retn=$retn)
            '''
            msgs = await core.stormlist(q)
            self.stormIsInPrint('retn is: hello', msgs)

            # Simple echo function
            q = '''
            function echo(arg) {
                return ($arg)
            }
            [(test:str=foo) (test:str=bar)]
            $retn=$echo($node.value())
            $lib.print('retn is: {retn}', retn=$retn)
            '''
            msgs = await core.stormlist(q)
            self.stormIsInPrint('retn is: foo', msgs)
            self.stormIsInPrint('retn is: bar', msgs)

            # Return value from a function based on a node value
            # inside of the function
            q = '''
            function echo(arg) {
                $lib.print('arg is {arg}', arg=$arg)
                [(test:str=1234) (test:str=5678)]
                return ($node.value())
            }
            [(test:str=foo) (test:str=bar)]
            $retn=$echo($node.value())
            $lib.print('retn is: {retn}', retn=$retn)
            '''
            msgs = await core.stormlist(q)
            self.stormIsInPrint('arg is foo', msgs)
            self.stormIsInPrint('arg is bar', msgs)
            self.stormIsInPrint('retn is: 1234', msgs)

            # Return values may be conditional
            q = '''function cond(arg) {
                if $arg {
                    return ($arg)
                } else {
                    // No action....
                }
            }
            [(test:int=0) (test:int=1)]
            $retn=$cond($node.value())
            $lib.print('retn is: {retn}', retn=$retn)
            '''
            msgs = await core.stormlist(q)
            self.stormIsInPrint('retn is: None', msgs)
            self.stormIsInPrint('retn is: 1', msgs)

            # Allow plumbing through args as keywords
            q = '''
            $test=$lib.import(test)
            $haha=$test.pprint('hello', 'world', arg3='goodbye')
            '''
            msgs = await core.stormlist(q)
            self.stormIsInPrint('arg1: hello', msgs)
            self.stormIsInPrint('arg2: world', msgs)
            self.stormIsInPrint('arg3: goodbye', msgs)
            # Allow plumbing through args out of order
            q = '''
            $test=$lib.import(test)
            $haha=$test.pprint(arg3='goodbye', arg1='hello', arg2='world')
            '''
            msgs = await core.stormlist(q)
            self.stormIsInPrint('arg1: hello', msgs)
            self.stormIsInPrint('arg2: world', msgs)
            self.stormIsInPrint('arg3: goodbye', msgs)

            # Basic function chaining
            q = '''
            function inner() {
                $lib.print("inner vertex")
                return ("foobarbazbiz")
            }

            function outer() {
                return ($inner())
            }

            $output = $outer()
            $lib.print($output)
            '''
            msgs = await core.stormlist(q)
            self.stormIsInPrint('inner vertex', msgs)
            self.stormIsInPrint('foobarbazbiz', msgs)

            # return a directly called function
            q = '''
            function woot(arg1) {
                return ( $($arg1 + 1) )
            }

            function squee(arg2) {
                return ($woot($arg2))
            }
            $output = $squee(17)
            $lib.print('output is {a}', a=$output)
            '''

            msgs = await core.stormlist(q)
            self.stormIsInPrint('output is 18', msgs)

            # recursive functions
            q = '''
            function recurse(cond, count) {
                if $( $cond = 15 ) {
                    return ($count)
                }
                return ($recurse( $($cond - 1), $($count + 1) ))
            }
            $output = $recurse(21, 0)
            $lib.print('final recursive output is {out}', out=$output)
            '''

            msgs = await core.stormlist(q)
            self.stormIsInPrint('final recursive output is 6', msgs)

            # return a function (not a value, but a ref to the function itself)
            q = '''
            function toreturn() {
                $lib.time.sleep(1)
                $lib.print('[{now}, "toreturn called"]', now=$($lib.time.now()))
                $lib.time.sleep(1)
                return ("foobar")
            }

            function wrapper() {
                return ($toreturn)
            }

            $func = $wrapper()
            $lib.print('[{now}, "this should be first"]', now=$($lib.time.now()))
            $output = $func()
            $lib.print('[{now}, "got {out}"]', now=$($lib.time.now()), out=$output)
            '''
            msgs = await core.stormlist(q)
            prints = list(filter(lambda m: m[0] == 'print', msgs))
            self.eq(len(prints), 3)

            jmsgs = list(map(lambda m: json.loads(m[1]['mesg']), prints))
            omsgs = sorted(jmsgs, key=lambda m: m[0])
            self.eq(omsgs[0][1], 'this should be first')
            self.eq(omsgs[1][1], 'toreturn called')
            self.eq(omsgs[2][1], 'got foobar')

            # module level global variables should be accessible to chained functions
            q = '''
            $biz = 0

            function bar() {
                $var1 = "subwoot"
                $var2 = "neato burrito"
                $biz = $( $biz + 10 )
                $lib.print($var2)
                return ("done")
            }

            function boop() {
                $retz = $bar()
                return ($retz)
            }

            function foo() {
                $var1 = "doublewoot"
                $retn = $bar()
                $lib.print($var1)
                return ($retn)
            }
            $lib.print($foo())
            $lib.print($boop())
            $lib.print("biz is now {biz}", biz=$biz)
            '''
            msgs = await core.stormlist(q)
            prints = list(filter(lambda m: m[0] == 'print', msgs))
            self.len(6, prints)
            self.stormIsInPrint("neato burrito", msgs)
            self.stormIsInPrint("done", msgs)
            self.stormIsInPrint("doublewoot", msgs)
            self.stormIsInPrint("biz is now 20", msgs)

            # test that the functions in a module don't pollute our own runts
            q = '''
            $test=$lib.import(test)
            $lib.print($outer("1337"))
            '''
            msgs = await core.stormlist(q)
            for msg in msgs:
                self.ne('print', msg[0])

            # make sure can set variables to the results of other functions in the same query
            q = '''
            function baz(arg1) {
                $lib.print('arg1={a}', a=$arg1)
                return ($arg1)
            }
            function bar(arg2) {
                $lib.print('arg2={a}', a=$arg2)
                $retn = $baz($arg2)
                return ($retn)
            }
            $foo = $bar("hehe")
            $lib.print($foo)
            '''
            msgs = await core.stormlist(q)
            self.stormIsInPrint('hehe', msgs)
            self.stormIsInPrint('arg1=hehe', msgs)
            self.stormIsInPrint('arg2=hehe', msgs)

            # call an import and have it's module local variables be mapped in to its own scope
            q = '''
            $test = $lib.import(importnest)
            $haha = $test.outer(False, $(33))
            $lib.print($haha)
            $hehe = $test.outer(True, $(17))
            $lib.print($hehe)
            $retn = $lib.import(importnest).outer(True, $(90))
            $lib.print($retn)
            $lib.print("counter is {c}", c$counter)
            '''
            msgs = await core.stormlist(q)
            prints = list(filter(lambda m: m[0] == 'print', msgs))
            self.len(9, prints)
            self.stormIsInPrint('counter is 0', msgs)
            self.stormIsInPrint('foobar is 33', msgs)
            self.stormIsInPrint('(Run: 0) we got back bar', msgs)
            self.stormIsInPrint('counter is 33', msgs)
            self.stormIsInPrint('foobar is 50', msgs)
            self.stormIsInPrint('(Run: 33) we got back foo', msgs)
            self.stormIsInPrint('counter is 0', msgs)
            self.stormIsInPrint('foobar is 90', msgs)
            self.stormIsInPrint('(Run: 0) we got back foo', msgs)

            # yields all the way down, no imports
            q = '''
            $count = 0
            function baz(arg3) {
                [ test:str = $arg3 ]
                $count = $( $count + 1)
                [ test:str = "cool" ]
            }

            function bar(arg2) {
                yield $baz($arg2)
            }

            function foo(arg1) {
                yield $bar($arg1)
            }

            yield $foo("bleeeergh")
            yield $foo("bloooop")
            $lib.print("nodes added: {c}", c=$count)
            '''
            msgs = await core.stormlist(q)
            self.stormIsInPrint('nodes added: 1', msgs)
            self.stormIsInPrint('nodes added: 2', msgs)

            # make sure local variables don't pollute up

            q = '''
            $global = $(346)
            function bar(arg1) {
                $lib.print("arg1 is {arg}", arg=$arg1)
                return ($arg1)
            }
            function foo(arg2) {
                $wat = $( $arg2 + 99 )
                $retn = $bar($wat)
                return ($retn)
            }
            $lib.print("retn is {ans}", ans=$( $foo($global)) )
            $lib.print("this should not print, but {wat}", wat=$wat)
            '''
            msgs = await core.stormlist(q)
            prints = list(filter(lambda m: m[0] == 'print', msgs))
            self.len(2, prints)
            self.stormIsInPrint('arg1 is 445', msgs)
            self.stormIsInPrint('retn is 445', msgs)

            # make sure we can't override the base lib object
            q = '''
            function wat(arg1) {
                $lib.print($arg1)
                $lib.print("We should have inherited the one true lib")
                return ("Hi :)")
            }
            function override() {
                $lib = "The new lib"
                $retn = $wat($lib)
                return ($retn)
            }

            $lib.print($override())
            $lib.print("NO OVERRIDES FOR YOU")
            '''
            msgs = await core.stormlist(q)
            self.stormIsInPrint('The new lib', msgs)
            self.stormIsInPrint('We should have inherited the one true lib', msgs)
            self.stormIsInPrint('Hi :)', msgs)
            self.stormIsInPrint('NO OVERRIDES FOR YOU', msgs)

            # don't override defined functions
            q = '''
            function nooverride(arg1) {
                $lib.print($arg1)
                return ("foobar")
            }

            function naughty() {
                $lib = "neato"
                $nooverride = $nooverride($lib)
                return ($nooverride)
            }

            $lib.print($naughty())
            $lib.print($nooverride("recovered"))
            '''

            msgs = await core.stormlist(q)
            self.stormIsInPrint('neato', msgs)
            self.stormIsInPrint('foobar', msgs)
            self.stormIsInPrint('recovered', msgs)

            # yields across an import boundary
            q = '''
            $test = $lib.import(yieldsforever)
            yield $test.yieldme("yieldsforimports")
            $lib.print($node.value())
            $lib.print("splat shouldn't exist, but we got {s}", s=$splat)
            '''
            msgs = await core.stormlist(q)
            self.stormIsInPrint('yieldsforimports', msgs)
            erfo = [m for m in msgs if m[0] == 'err'][0]
            self.eq(erfo[1][0], 'NoSuchVar')
            self.eq(erfo[1][1].get('name'), 'splat')

            # Too few args are problematic
            q = '''
            $test=$lib.import(test)
            $haha=$test.pprint('hello', 'world')
            '''
            msgs = await core.stormlist(q)
            erfo = [m for m in msgs if m[0] == 'err'][0]
            self.eq(erfo[1][0], 'StormRuntimeError')
            self.eq(erfo[1][1].get('name'), 'pprint')
            self.eq(erfo[1][1].get('expected'), 3)
            self.eq(erfo[1][1].get('got'), 2)

            # Too few args are problematic - kwargs edition
            q = '''
            $test=$lib.import(test)
            $haha=$test.pprint(arg1='hello', arg2='world')
            '''
            msgs = await core.stormlist(q)
            erfo = [m for m in msgs if m[0] == 'err'][0]
            self.eq(erfo[1][0], 'StormRuntimeError')
            self.eq(erfo[1][1].get('name'), 'pprint')
            self.eq(erfo[1][1].get('expected'), 3)
            self.eq(erfo[1][1].get('got'), 2)

            # unused kwargs are fatal
            q = '''
            $test=$lib.import(test)
            $haha=$test.pprint('hello', 'world', arg3='world', arg4='newp')
            '''
            msgs = await core.stormlist(q)
            erfo = [m for m in msgs if m[0] == 'err'][0]
            self.eq(erfo[1][0], 'StormRuntimeError')
            self.eq(erfo[1][1].get('name'), 'pprint')
            self.eq(erfo[1][1].get('kwargs'), ['arg4'])

            # kwargs which duplicate a positional arg is fatal
            q = '''
            $test=$lib.import(test)
            $haha=$test.pprint('hello', 'world', arg1='hello')
            '''
            msgs = await core.stormlist(q)
            erfo = [m for m in msgs if m[0] == 'err'][0]
            self.eq(erfo[1][0], 'StormRuntimeError')
            self.eq(erfo[1][1].get('name'), 'pprint')
            self.eq(erfo[1][1].get('kwargs'), ['arg1'])

            # Too many args are fatal too
            q = '''
            $test=$lib.import(test)
            $haha=$test.pprint('hello', 'world', 'goodbye', 'newp')
            '''
            msgs = await core.stormlist(q)
            erfo = [m for m in msgs if m[0] == 'err'][0]
            self.eq(erfo[1][0], 'StormRuntimeError')
            self.eq(erfo[1][1].get('name'), 'pprint')
            self.eq(erfo[1][1].get('valu'), 'newp')

    async def test_ast_function_scope(self):

        async with self.getTestCore() as core:

            nodes = await core.nodes('''

                function foo (x) {
                    [ test:str=$x ]
                }

                yield $foo(asdf)

            ''')

            self.len(1, nodes)
            self.eq(nodes[0].ndef, ('test:str', 'asdf'))

            scmd = {
                'name': 'foocmd',
                'storm': '''

                    function lulz (lulztag) {
                        [ test:str=$lulztag ]
                    }

                    for $tag in $node.tags() {
                        yield $lulz($tag)
                    }

                ''',
            }

            await core.setStormCmd(scmd)

            nodes = await core.nodes('[ inet:ipv4=1.2.3.4 +#visi ] | foocmd')
            self.eq(nodes[0].ndef, ('test:str', 'visi'))
            self.eq(nodes[1].ndef, ('inet:ipv4', 0x01020304))

            async with await core.snap() as snap:
                with snap.getStormRuntime() as runt:
                    q = '''
                    function lolol() {
                        $lib = "pure lulz"
                        $lolol = "don't do this"
                        return ($lolol)
                    }
                    $neato = 0
                    $myvar = $lolol()
                    $lib.print($myvar)
                    '''
                    query = core.getStormQuery(q)
                    runt.loadRuntVars(query)
                    async for item in query.run(runt, s_common.agen()):
                        pass
                    func = list(filter(lambda o: isinstance(o, s_ast.Function), query.kids))[0]
                    oldfunc = runt.vars['lolol']
                    funcrunt = await runt.getScopeRuntime(func.kids[2])
                    async for item in func.run(funcrunt, s_common.agen()):
                        pass
                    funcrunt.globals.add('nope')
                    funcrunt.globals.add('lolol')
                    self.eq(oldfunc, runt.vars['lolol'])
                    await runt.propBackGlobals(funcrunt)
                    self.notin('nope', runt.runtvars)

    async def test_ast_setitem(self):

        async with self.getTestCore() as core:

            q = '''
                $x = asdf
                $y = $lib.dict()

                $y.foo = bar
                $y."baz faz" = hehe
                $y.$x = qwer

                for ($name, $valu) in $y {
                    [ test:str=$name test:str=$valu ]
                }
            '''
            nodes = await core.nodes(q)
            self.len(6, nodes)
            self.eq(nodes[0].ndef[1], 'foo')
            self.eq(nodes[1].ndef[1], 'bar')
            self.eq(nodes[2].ndef[1], 'baz faz')
            self.eq(nodes[3].ndef[1], 'hehe')
            self.eq(nodes[4].ndef[1], 'asdf')
            self.eq(nodes[5].ndef[1], 'qwer')

            # non-runtsafe test
            q = '''$dict = $lib.dict()
            [(test:str=key1 :hehe=val1) (test:str=key2 :hehe=val2)]
            $key=$node.value()
            $dict.$key=:hehe
            fini {
                $lib.fire(event, dict=$dict)
            }
            '''
            mesgs = await core.stormlist(q)
            stormfire = [m for m in mesgs if m[0] == 'storm:fire']
            self.len(1, stormfire)
            self.eq(stormfire[0][1].get('data').get('dict'),
                    {'key1': 'val1', 'key2': 'val2'})

            # The default StormType does not support item assignment
            q = '''
            $set=$lib.set()
            $set.foo="bar"
            '''
            msgs = await core.stormlist(q)
            erfo = [m for m in msgs if m[0] == 'err'][0]
            self.eq(erfo[1][0], 'StormRuntimeError')
            self.eq(erfo[1][1].get('mesg'), 'Set does not support assignment.')

    async def test_ast_initfini(self):

        async with self.getTestCore() as core:

            q = '''
                init { $x = $(0) }

                [ test:int=$x ]

                init { $x = $( $x + 2 ) $lib.fire(lulz, x=$x) }

                [ test:int=$x ]

                [ +#foo ]

                fini { $lib.print('xfini: {x}', x=$x) }
            '''

            msgs = await core.stormlist(q)

            types = [m[0] for m in msgs if m[0] in ('node', 'print', 'storm:fire')]
            self.eq(types, ('storm:fire', 'node', 'node', 'print'))

            nodes = [m[1] for m in msgs if m[0] == 'node']

            self.eq(nodes[0][0], ('test:int', 0))
            self.eq(nodes[1][0], ('test:int', 2))

            nodes = await core.nodes('init { [ test:int=20 ] }')
            self.len(1, nodes)
            self.eq(nodes[0].ndef, ('test:int', 20))

            # init and fini blocks may also yield nodes
            q = '''
            init {
                [(test:str=init1 :hehe=hi)]
            }
            $hehe=:hehe
            [test:str=init2 :hehe=$hehe]
            '''
            nodes = await core.nodes(q)
            self.eq(nodes[0].ndef, ('test:str', 'init1'))
            self.eq(nodes[0].get('hehe'), 'hi')
            self.eq(nodes[1].ndef, ('test:str', 'init2'))
            self.eq(nodes[1].get('hehe'), 'hi')

            # Non-runtsafe init fails to execute
            q = '''
            test:str^=init +:hehe $hehe=:hehe
            init {
                [test:str=$hehe]
            }
            '''
            msgs = await core.stormlist(q)
            erfo = [m for m in msgs if m[0] == 'err'][0]
            self.eq(erfo[1][0], 'StormRuntimeError')
            self.eq(erfo[1][1].get('mesg'), 'Init block query must be runtsafe')

            # Runtsafe init works and can yield nodes, this has inbound nodes as well
            q = '''
            test:str^=init
            $hehe="const"
            init {
                [test:str=$hehe]
            }
            '''
            nodes = await core.nodes(q)
            self.eq(nodes[0].ndef, ('test:str', 'const'))
            self.eq(nodes[1].ndef, ('test:str', 'init1'))
            self.eq(nodes[2].ndef, ('test:str', 'init2'))

            # runtsafe fini with a node example which works
            q = '''
            [test:str=fini1 :hehe=bye]
            $hehe="hehe"
            fini {
                [(test:str=fini2 :hehe=$hehe)]
            }
            '''
            nodes = await core.nodes(q)
            self.eq(nodes[0].ndef, ('test:str', 'fini1'))
            self.eq(nodes[0].get('hehe'), 'bye')
            self.eq(nodes[1].ndef, ('test:str', 'fini2'))
            self.eq(nodes[1].get('hehe'), 'hehe')

            # Non-runtsafe fini example which fails
            q = '''
            [test:str=fini3 :hehe="number3"]
            $hehe=:hehe
            fini {
                [(test:str=fini4 :hehe=$hehe)]
            }
            '''
            msgs = await core.stormlist(q)
            erfo = [m for m in msgs if m[0] == 'err'][0]
            self.eq(erfo[1][0], 'StormRuntimeError')
            self.eq(erfo[1][1].get('mesg'), 'Fini block query must be runtsafe')

            # Tally use - case example for counting
            q = '''
            init {
                $tally = $lib.stats.tally()
            }
            test:int $tally.inc('node') | spin |
            fini {
                for ($name, $total) in $tally {
                    $lib.fire(name=$name, total=$total)
                }
            }
            '''
            msgs = await core.stormlist(q)
            firs = [m for m in msgs if m[0] == 'storm:fire']
            self.len(1, firs)
            evnt = firs[0]
            self.eq(evnt[1].get('data'), {'total': 3})

    async def test_ast_cmdargs(self):

        async with self.getTestCore() as core:

            scmd = {
                'name': 'foo',
                'cmdargs': (
                    ('--bar', {}),
                ),
                'storm': '''
                    $ival = $lib.cast(ival, $cmdopts.bar)
                    [ test:str=1234 +#foo=$ival ]
                ''',
            }

            await core.setStormCmd(scmd)

            nodes = await core.nodes('foo --bar (2020,2021) | +#foo@=202002')
            self.len(1, nodes)

            scmd = {
                'name': 'baz',
                'cmdargs': (
                    ('--faz', {}),
                ),
                'storm': '''
                    // subquery forces per-node evaluation of even runt safe vars
                    {
                        $ival = $lib.cast(ival, $cmdopts.faz)
                        [ test:str=beep +#foo=$ival ]
                    }
                ''',
            }

            await core.setStormCmd(scmd)

            await core.nodes('[ test:int=5678 +#foo=(2018, 2021) ]')
            await core.nodes('[ test:int=1111 +#foo=(1977, 2019) ]')

            nodes = await core.nodes('test:int | baz --faz #foo')
            self.len(2, nodes)

            nodes = await core.nodes('test:str +#foo@=1984 +#foo@=202002')
            self.len(1, nodes)
            self.eq(nodes[0].ndef, ('test:str', 'beep'))

    async def test_ast_pullone(self):

        async def genr():
            yield 1
            yield 2
            yield 3

        vals = [x async for x in await s_ast.pullone(genr())]
        self.eq((1, 2, 3), vals)

        data = {}
        async def empty():
            data['executed'] = True
            if 0: yield None

        vals = [x async for x in await s_ast.pullone(empty())]
        self.eq([], vals)
        self.true(data.get('executed'))

        async def hasone():
            yield 1

        vals = [x async for x in await s_ast.pullone(hasone())]
        self.eq((1,), vals)
