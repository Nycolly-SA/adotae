"""Remoção lógica em PostgreSQL descartável; sem chamadas ao Supabase remoto."""

import unittest
import uuid

import test_adocao_local as adocao
import test_publicacao_local as publicacao
import test_animal_fotos_local as fotos
from test_animal_fotos_local import A, B, claims, literal


MIGRATION = fotos.ROOT / 'supabase/migrations/202611120001_remover_anuncio.sql'


class RemocaoSQLLocal(adocao.AdocaoSQLLocal, publicacao.PublicacaoSQLLocal):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        storage_acl = cls.execute("select relacl from pg_class where oid='storage.objects'::regclass;")
        cls.execute(MIGRATION.read_text(encoding='utf-8'))
        if storage_acl != cls.execute("select relacl from pg_class where oid='storage.objects'::regclass;"):
            raise AssertionError('Remoção alterou grants do Storage')
        # As regressões comparam o schema atual, incluindo as alterações
        # deliberadas de DELETE/RLS desta migração, sem modificar as anteriores.
        cls.before = cls.audit()
        cls.storage_before = cls.storage_policy_metadata()
        cls.fotos_before = cls.fotos_metadata()

    def remover(self, animal=None, privada=False):
        schema = 'adotae_private' if privada else 'public'
        return f"select {schema}.remover_anuncio('{animal or self.animal}');"

    def test_remocao_estados_preservacao_e_repeticao(self):
        for publicado in [False, True]:
            for oculto in [False, True]:
                with self.subTest(publicado=publicado, oculto=oculto):
                    self.run_case((self.publico() if publicado else '') + f"""
                    update public.animais set oculto_admin={str(oculto).lower()} where id='{self.animal}';
                    create temp table antes as select to_jsonb(a) dados from public.animais a;
                    create temp table fotos_antes as select jsonb_agg(to_jsonb(f)) dados from public.animal_fotos f;
                    create temp table objetos_antes as select jsonb_agg(to_jsonb(o)) dados from storage.objects o;
                    {claims()} {self.remover()}
                    reset role;
                    select pg_temp.check_true((select removido_em is not null
                        and to_jsonb(a)-array['removido_em','atualizado_em']
                            =antes.dados-array['removido_em','atualizado_em'] from public.animais a,antes));
                    create temp table removido as select to_jsonb(a) dados,ctid::text versao,xmin::text transacao from public.animais a;
                    {claims()} {self.remover()}
                    reset role;
                    select pg_temp.check_true((select to_jsonb(a)=r.dados and a.ctid::text=r.versao
                        and a.xmin::text=r.transacao from public.animais a,removido r));
                    select pg_temp.check_true((select jsonb_agg(to_jsonb(f)) from public.animal_fotos f)=(select dados from fotos_antes));
                    select pg_temp.check_true((select jsonb_agg(to_jsonb(o)) from storage.objects o)=(select dados from objetos_antes));
                    """, self.objeto())

    def test_remocao_identidade_estados_e_auxiliar(self):
        for privada in [False, True]:
            for contexto in [claims(B), claims(''), 'set local role anon;',
                             claims() + 'set local role service_role;']:
                with self.subTest(privada=privada, contexto=contexto):
                    self.run_case(contexto + f"select pg_temp.expect_error({literal(self.remover(privada=privada))},'42501');")
            for publicado in [False, True]:
                self.run_case((self.publico() if publicado else '')
                    + f"update public.animais set status_adocao='adotado' where id='{self.animal}';"
                    + claims() + f"select pg_temp.expect_error({literal(self.remover(privada=privada))},'42501');")
            self.run_case(claims() + self.remover(privada=privada))
        for chamada in ['select public.remover_anuncio(null);', self.remover(str(uuid.uuid4()))]:
            self.run_case(claims() + f"select pg_temp.expect_error({literal(chamada)},'42501');")

    def test_remocao_delete_e_coluna_protegida(self):
        self.run_case(claims() + f"""
        select pg_temp.expect_error('delete from public.animais where id=''{self.animal}''','42501');
        select pg_temp.expect_error('update public.animais set removido_em=now()','42501');
        reset role;
        grant update(removido_em) on public.animais to authenticated;
        {claims()}
        select pg_temp.expect_error('update public.animais set removido_em=now()','42501');
        """)

    def test_remocao_bloqueia_mutacoes(self):
        for publicado in [False, True]:
            self.run_case((self.publico() if publicado else '') + claims() + self.remover() + f"""
            with alterados as (update public.animais set email_contato='novo@example.org' returning id)
            select pg_temp.check_true((select count(*)=0 from alterados));
            with alteradas as (update public.animal_fotos set estado='removendo' returning id)
            select pg_temp.check_true((select count(*)=0 from alteradas));
            select pg_temp.expect_error('insert into public.animal_fotos(animal_id,posicao) values (''{self.animal}'',2)','42501');
            select pg_temp.expect_error({literal(self.confirmar())},'42501');
            select pg_temp.expect_error('select public.publicar_anuncio(''{self.animal}'')','42501');
            select pg_temp.expect_error({literal(self.adotar())},'42501');
            """, self.objeto())

    def test_remocao_recusa_restauracao_explicita(self):
        restaurar = f"update public.animais set removido_em=null where id='{self.animal}'"
        self.run_case(claims() + self.remover() + f"""
        select pg_temp.expect_error({literal(restaurar)},'42501');
        reset role;
        grant update(removido_em) on public.animais to authenticated;
        {claims()}
        with alterados as ({restaurar} returning id)
        select pg_temp.check_true((select count(*)=0 from alterados));
        reset role;
        -- Mesmo contornando a RLS como operador local, o trigger impede restaurar.
        select pg_temp.expect_error({literal(restaurar)},'42501');
        select pg_temp.check_true((select removido_em is not null from public.animais where id='{self.animal}'));
        """)

    def test_remocao_visibilidade_e_storage(self):
        # Bases permissivas adicionais não devem contornar as barreiras.
        amplo = """
        create policy teste_ampla on public.animais for select to public using(true);
        create policy teste_fotos_ampla on public.animal_fotos for select to public using(true);
        create policy teste_storage_ampla on storage.objects for all to public using(true) with check(true);
        """
        for contexto in [self.anon(), claims(B), claims()]:
            for op in ['object.sign', 'object.get_authenticated', 'object.get_authenticated_info',
                       'object.upload', 'object.delete', 'object.delete_many', 'object.list']:
                with self.subTest(contexto=contexto, op=op):
                    dono = contexto == claims()
                    self.run_case(self.publico() + amplo + claims() + self.remover()
                        + 'reset role;' + contexto + f"""
                        select set_config('storage.operation',{literal(op)},true);
                        select pg_temp.check_true((select count(*)={1 if dono else 0} from public.animais));
                        select pg_temp.check_true((select count(id)={1 if dono else 0} from public.animal_fotos));
                        select pg_temp.check_true(not exists(select 1 from storage.objects));
                        with apagados as (delete from storage.objects returning *)
                        select pg_temp.check_true((select count(*)=0 from apagados));
                        select pg_temp.expect_error({literal(self.objeto(path=self.path + 'novo'))},'42501');
                        """, self.objeto())

    def test_remocao_permissoes_e_funcoes(self):
        self.run_case("""
        select pg_temp.check_true(not has_table_privilege('authenticated','public.animais','DELETE'));
        select pg_temp.check_true(not exists(select 1 from pg_policies
            where schemaname='public' and tablename='animais' and policyname='animais_exclusao_propria'));
        select pg_temp.check_true(not has_column_privilege('authenticated','public.animais','removido_em',p))
            from unnest(array['INSERT','UPDATE']) p;
        select pg_temp.check_true(p.prosecdef=(n.nspname='adotae_private')
            and p.proconfig=array['search_path=""'] and pg_get_userbyid(p.proowner)='postgres'
            and p.prorettype='void'::regtype and p.proargnames=array['p_animal_id'])
        from pg_proc p join pg_namespace n on n.oid=p.pronamespace where p.oid in
            ('public.remover_anuncio(uuid)'::regprocedure,'adotae_private.remover_anuncio(uuid)'::regprocedure);
        select pg_temp.check_true(has_function_privilege('authenticated',f,'EXECUTE')
            and not has_function_privilege('anon',f,'EXECUTE')
            and not has_function_privilege('service_role',f,'EXECUTE'))
        from unnest(array['public.remover_anuncio(uuid)','adotae_private.remover_anuncio(uuid)']) f;
        select pg_temp.check_true(not exists(select 1 from pg_proc p, lateral aclexplode(p.proacl) acl
            where p.oid in ('public.remover_anuncio(uuid)'::regprocedure,
                'adotae_private.remover_anuncio(uuid)'::regprocedure) and acl.grantee=0));
        select pg_temp.check_true(not has_schema_privilege('authenticated','adotae_private','CREATE'));
        """)

    def test_remocao_preserva_reservas_em_todos_estados(self):
        for estado in ['pendente', 'pronta', 'removendo']:
            with self.subTest(estado=estado):
                alterar = ('' if estado == 'pendente' else
                           f"update public.animal_fotos set estado='{estado}' where id='{self.foto}';")
                self.run_case(alterar + claims() + self.remover() + f"""
                select pg_temp.expect_error({literal(self.confirmar())},'42501');
                select set_config('storage.operation','object.delete',true);
                with apagados as (delete from storage.objects returning *)
                select pg_temp.check_true((select count(*)=0 from apagados));
                reset role;
                select pg_temp.check_true((select estado='{estado}' from public.animal_fotos where id='{self.foto}'));
                select pg_temp.check_true((select count(*)=1 from storage.objects where name={literal(self.path)}));
                """, self.objeto())
        # Caminho legítimo e reserva pendente: upload novo deve ser recusado.
        self.run_case(claims() + self.remover()
            + "select set_config('storage.operation','object.upload',true);"
            + f"select pg_temp.expect_error({literal(self.objeto())},'42501');")

    def test_remocao_estados_inesperados_e_repeticao_revalidada(self):
        for publicado in [False, True]:
            for oculto in [False, True]:
                for removido in [False, True]:
                    with self.subTest(publicado=publicado, oculto=oculto, removido=removido):
                        # Só o operador local simula combinações legadas/corrompidas.
                        self.run_case((self.publico() if publicado else '') + f"""
                        alter table public.animais disable trigger animais_proteger_remocao;
                        update public.animais set status_adocao='adotado',oculto_admin={str(oculto).lower()},
                            removido_em={'now()' if removido else 'null'} where id='{self.animal}';
                        alter table public.animais enable trigger animais_proteger_remocao;
                        {claims()}
                        select pg_temp.expect_error({literal(self.remover())},'42501');
                        """)
        self.run_case(claims() + self.remover() + claims(B)
            + f"select pg_temp.expect_error({literal(self.remover())},'42501');")

    def test_remocao_migracao_recusa_grants_herdados(self):
        papel = 'remocao_test_' + uuid.uuid4().hex
        for grant, erro in [
            ('grant delete on public.animais to public;', 'DELETE efetivo'),
            (f'create role {papel}; grant {papel} to authenticated;'
             f'grant delete on public.animais to {papel};', 'DELETE efetivo'),
            ('grant update on public.animais to authenticated;', 'escrita efetiva'),
            ('grant insert(removido_em) on public.animais to public;', 'escrita efetiva'),
        ]:
            with self.subTest(grant=grant):
                # Simular schema anterior só nesta transação. A migração aborta
                # antes de recriar funções; o fechamento reverte também o papel.
                setup = ('begin; alter table public.animais drop column removido_em cascade;'
                         'create policy animais_exclusao_propria on public.animais for delete '
                         'to authenticated using(usuario_id=auth.uid());')
                if 'insert(removido_em)' in grant:
                    # Grant de tabela alcançará também a nova coluna.
                    grant = 'grant insert on public.animais to public;'
                with self.assertRaisesRegex(AssertionError, erro):
                    self.execute(setup + grant + MIGRATION.read_text(encoding='utf-8'))
                self.assertEqual(self.before, self.audit())

    def test_remocao_concorrencia_operacoes(self):
        for operacao in ['editar', 'confirmar', 'confirmar_pronta', 'reservar', 'foto', 'publicar', 'adotar', 'remover']:
            for remove_primeiro in [False, True]:
                with self.subTest(operacao=operacao, remove_primeiro=remove_primeiro):
                    preparo = (self.publico() if operacao == 'adotar' else
                               self.preparar() if operacao == 'publicar' else
                               f"update public.animal_fotos set estado='pronta' where id='{self.foto}';"
                               if operacao == 'confirmar_pronta' else '')
                    self.execute(self.fixtures + self.objeto() + preparo)
                    outra = claims() + {
                        'editar': f"update public.animais set bairro='Concorrente' where id='{self.animal}';",
                        'confirmar': self.confirmar(),
                        'confirmar_pronta': self.confirmar(),
                        'reservar': f"insert into public.animal_fotos(animal_id,posicao) values ('{self.animal}',2);",
                        'foto': f"update public.animal_fotos set estado='removendo' where id='{self.foto}';",
                        'publicar': self.publicar(), 'adotar': self.adotar(), 'remover': self.remover(),
                    }[operacao]
                    remover = claims() + self.remover()
                    try:
                        code, error = self.concorrer(
                            remover if remove_primeiro else outra,
                            outra if remove_primeiro else remover,
                            segunda_pode_recusar=remove_primeiro and operacao == 'publicar')
                        recusa = (remove_primeiro and operacao in ['confirmar','confirmar_pronta','reservar','foto','publicar','adotar']) or (
                            not remove_primeiro and operacao == 'adotar')
                        if recusa:
                            self.assertNotEqual(code, 0)
                            self.assertIn('55P03' if remove_primeiro and operacao == 'publicar' else '42501', error)
                        else:
                            self.assertEqual(code, 0, error)
                        removido = not (not remove_primeiro and operacao == 'adotar')
                        self.assertEqual(self.execute(f"select removido_em is not null from public.animais where id='{self.animal}';"),
                                         't' if removido else 'f')
                        if remove_primeiro:
                            self.assertEqual(self.execute(f"select estado from public.animal_fotos where id='{self.foto}';"),
                                             'pronta' if operacao in ['confirmar_pronta','publicar','adotar'] else 'pendente')
                            self.assertEqual(self.execute(f"select count(*) from public.animal_fotos where animal_id='{self.animal}';"), '1')
                            self.assertEqual(self.execute(f"select bairro is null from public.animais where id='{self.animal}';"), 't')
                    finally:
                        self.execute(f"delete from storage.objects where name={literal(self.path)};"
                                     f"delete from public.animal_fotos where animal_id='{self.animal}';"
                                     f"delete from public.animais where id='{self.animal}';")
                        self.setUp()


if __name__ == '__main__':
    unittest.main(verbosity=2)
