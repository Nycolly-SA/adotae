"""Adoção com SQL/RLS reais no PostgreSQL descartável; Auth/Storage são stubs."""

import unittest
import uuid

import test_animal_fotos_local as fotos
from test_animal_fotos_local import A, B, claims, literal
import test_fotos_publicas_local as publicas


MIGRATION = fotos.ROOT / 'supabase/migrations/202611100001_marcar_adotado.sql'


class AdocaoSQLLocal(publicas.FotosPublicasSQLLocal):
    # Reexecutar também as regressões de fotos e visibilidade após a migração.
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        antes = cls.protecoes()
        cls.execute(MIGRATION.read_text(encoding='utf-8'))
        if antes != cls.protecoes():
            raise AssertionError('Adoção alterou grants, RLS ou triggers existentes')

    @classmethod
    def protecoes(cls):
        return cls.execute("""select jsonb_build_object(
        'acl', (select jsonb_agg(jsonb_build_array(c.oid,c.relacl,c.relrowsecurity,c.relforcerowsecurity)
            order by c.oid) from pg_class c where c.oid in
            ('public.animais'::regclass,'public.animal_fotos'::regclass,'storage.objects'::regclass)),
        'colunas', (select jsonb_agg(jsonb_build_array(attrelid,attnum,attacl) order by attrelid,attnum)
            from pg_attribute where attrelid in
            ('public.animais'::regclass,'public.animal_fotos'::regclass,'storage.objects'::regclass)),
        'rls', (select jsonb_agg(to_jsonb(p) order by schemaname,tablename,policyname)
            from pg_policies p),
        'triggers', (select jsonb_agg(to_jsonb(t) order by oid) from pg_trigger t),
        'funcoes_trigger', (select jsonb_agg(to_jsonb(p) order by p.oid) from pg_proc p
            where p.oid in (select tgfoid from pg_trigger)),
        'schemas', (select jsonb_agg(jsonb_build_array(nspname,nspacl) order by nspname)
            from pg_namespace where nspname in ('public','adotae_private','storage')));""")

    def adotar(self, animal=None, privada=False):
        schema = 'adotae_private' if privada else 'public'
        return f"select {schema}.marcar_adotado('{animal or self.animal}');"

    def test_adocao_preserva_campos_fotos_objetos_e_atualiza_data(self):
        # A data da fixture é anterior à chamada, independente da resolução do relógio.
        self.run_case(self.publico() + f"""
        alter table public.animais disable trigger animais_atualizar_data;
        update public.animais set atualizado_em='2000-01-01' where id='{self.animal}';
        alter table public.animais enable trigger animais_atualizar_data;
        create temp table antes as select to_jsonb(a) dados from public.animais a where id='{self.animal}';
        create temp table fotos_antes as select to_jsonb(f) dados from public.animal_fotos f;
        create temp table objetos_antes as select to_jsonb(o) dados from storage.objects o;
        create temp table chamada as select clock_timestamp() inicio;
        {claims()} {self.adotar()}
        select pg_temp.check_true((select status_adocao='adotado' from public.animais where id='{self.animal}'));
        reset role;
        select pg_temp.check_true((select
            to_jsonb(a)-array['status_adocao','atualizado_em']=antes.dados-array['status_adocao','atualizado_em']
            and a.atualizado_em > (antes.dados->>'atualizado_em')::timestamptz
            and a.atualizado_em >= chamada.inicio and a.atualizado_em <= clock_timestamp()
            from public.animais a,antes,chamada where id='{self.animal}'));
        select pg_temp.check_true((select jsonb_agg(to_jsonb(f) order by id) from public.animal_fotos f)
            =(select jsonb_agg(dados order by dados->>'id') from fotos_antes));
        select pg_temp.check_true((select jsonb_agg(to_jsonb(o) order by id) from storage.objects o)
            =(select jsonb_agg(dados order by dados->>'id') from objetos_antes));
        """, self.objeto())

    def test_recusa_papeis_e_identidade_invalida(self):
        for contexto in [claims(B), claims(''), 'set local role anon;',
                         claims() + 'set local role service_role;']:
            for privada in [False, True]:
                with self.subTest(contexto=contexto, privada=privada):
                    self.run_case(self.publico() + contexto
                                  + f"select pg_temp.expect_error({literal(self.adotar(privada=privada))},'42501');"
                                  + "reset role; select pg_temp.check_true((select status_adocao='disponivel' from public.animais));")

    def test_recusa_rascunho_inexistente_e_nulo(self):
        for chamada in [self.adotar(), self.adotar(str(uuid.uuid4())),
                        'select public.marcar_adotado(null);']:
            with self.subTest(chamada=chamada):
                self.run_case(claims() + f"select pg_temp.expect_error({literal(chamada)},'42501');")

    def test_segunda_tentativa_recusada_sem_nova_versao(self):
        self.run_case(self.publico() + claims() + self.adotar() + f"""
        reset role;
        create temp table antes as select to_jsonb(a) dados,ctid::text localizacao,xmin::text versao
            from public.animais a where id='{self.animal}';
        {claims()}
        select pg_temp.expect_error({literal(self.adotar())},'42501');
        reset role;
        select pg_temp.check_true((select to_jsonb(a)=antes.dados and a.ctid::text=antes.localizacao
            and a.xmin::text=antes.versao from public.animais a,antes where id='{self.animal}'));
        """)

    def test_oculto_pode_ser_adotado_sem_reativacao(self):
        self.run_case(self.publico() + f"update public.animais set oculto_admin=true where id='{self.animal}';"
                      + claims() + self.adotar() + f"""
        select pg_temp.check_true((select oculto_admin and status_adocao='adotado'
            and status_publicacao='publicado' from public.animais where id='{self.animal}'));
        """)

    def test_update_direto_e_reversao_continuam_bloqueados(self):
        self.run_case(self.publico() + claims() + f"""
        select pg_temp.expect_error('update public.animais set status_adocao=''adotado'' where id=''{self.animal}''','42501');
        {self.adotar()}
        select pg_temp.expect_error('update public.animais set status_adocao=''disponivel'' where id=''{self.animal}''','42501');
        -- Mesmo um grant acidental na transação não contorna o trigger existente.
        reset role;
        grant update(status_adocao) on public.animais to authenticated;
        {claims()}
        select pg_temp.expect_error('update public.animais set status_adocao=''disponivel'' where id=''{self.animal}''','42501');
        """)

    def test_permissoes_funcoes(self):
        self.run_case("""
        select pg_temp.check_true(count(*)=2) from pg_proc p where p.oid in
            ('public.marcar_adotado(uuid)'::regprocedure,'adotae_private.marcar_adotado(uuid)'::regprocedure);
        select pg_temp.check_true(p.prosecdef=(n.nspname='adotae_private')
            and p.proconfig=array['search_path=""'] and pg_get_userbyid(p.proowner)='postgres'
            and p.prorettype='void'::regtype and p.proargnames=array['p_animal_id'])
        from pg_proc p join pg_namespace n on n.oid=p.pronamespace where p.oid in
            ('public.marcar_adotado(uuid)'::regprocedure,'adotae_private.marcar_adotado(uuid)'::regprocedure);
        select pg_temp.check_true(has_function_privilege('authenticated',f,'EXECUTE')
            and not has_function_privilege('anon',f,'EXECUTE')
            and not has_function_privilege('service_role',f,'EXECUTE'))
        from unnest(array['public.marcar_adotado(uuid)','adotae_private.marcar_adotado(uuid)']) f;
        select pg_temp.check_true(not exists(select 1 from pg_proc p,
            lateral aclexplode(p.proacl) acl where p.oid in
            ('public.marcar_adotado(uuid)'::regprocedure,'adotae_private.marcar_adotado(uuid)'::regprocedure)
            and (acl.grantee=0 or acl.privilege_type <> 'EXECUTE' or
                 acl.grantee not in ('postgres'::regrole,'authenticated'::regrole))));
        select pg_temp.check_true(not has_column_privilege('authenticated','public.animais','status_adocao','UPDATE'));
        """)
        self.run_case(self.publico() + claims() + self.adotar(privada=True))

    def test_migracao_recusa_update_efetivo(self):
        migration = MIGRATION.read_text(encoding='utf-8')
        for grant in ['grant update on public.animais to authenticated;',
                      'grant update(status_adocao) on public.animais to public;']:
            with self.subTest(grant=grant):
                with self.assertRaisesRegex(AssertionError, 'authenticated mantém UPDATE efetivo'):
                    self.execute('begin;' + grant + migration)
                self.assertEqual(self.execute("select has_column_privilege("
                    "'authenticated','public.animais','status_adocao','UPDATE');"), 'f')

    def test_visibilidade_apos_adocao_e_acesso_do_dono(self):
        self.novo_caso(self.anon() + self.verificar_objetos(1) + f"""
        select pg_temp.check_true((select count(*)=1 from public.animais where id='{self.animal}'));
        select pg_temp.check_true((select count(*)=1 from public.animal_fotos where id='{self.foto}'));
        {claims()} {self.adotar()}
        select set_config('storage.operation','object.get_authenticated',true);
        """ + self.verificar_objetos(1) + f"""
        select pg_temp.check_true((select count(*)=1 from public.animais where id='{self.animal}'));
        select pg_temp.check_true((select count(*)=1 from public.animal_fotos where id='{self.foto}'));
        """ + self.anon() + self.verificar_objetos(0) + f"""
        select pg_temp.check_true(not exists(select 1 from public.animais where id='{self.animal}'));
        select pg_temp.check_true(not exists(select id from public.animal_fotos where id='{self.foto}'));
        {claims(B)}
        select pg_temp.check_true(not exists(select 1 from public.animais where id='{self.animal}'));
        select pg_temp.check_true(not exists(select 1 from public.animal_fotos where id='{self.foto}'));
        """ + self.verificar_objetos(0))

    def test_duas_adocoes_concorrentes(self):
        self.execute(self.fixtures + self.publico())
        try:
            # Snapshot persistente após a primeira chamada, antes de liberar a segunda.
            antes = f"reset role; create table adocao_antes as select to_jsonb(a) dados,ctid::text localizacao from public.animais a where id='{self.animal}';"
            code, erro = self.concorrer(claims() + self.adotar(), claims() + self.adotar(), antes)
            self.assertNotEqual(code, 0)
            self.assertIn('42501', erro)
            self.assertEqual(self.execute(f"select status_adocao='adotado' and to_jsonb(a)=b.dados "
                f"and a.ctid::text=b.localizacao from public.animais a,adocao_antes b where id='{self.animal}';"), 't')
        finally:
            self.execute(f"drop table if exists adocao_antes; delete from public.animal_fotos where animal_id='{self.animal}';"
                         f"delete from public.animais where id='{self.animal}';")


if __name__ == '__main__':
    unittest.main(verbosity=2)
