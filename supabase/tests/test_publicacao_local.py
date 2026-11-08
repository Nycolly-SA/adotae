"""Publicação SQL no mesmo PostgreSQL descartável da suíte de fotografias."""

import unittest
import uuid

import test_animal_fotos_local as fotos
from test_animal_fotos_local import A, B, claims, literal


class PublicacaoSQLLocal(fotos.FotosSQLLocal):
    def publicar(self, animal=None):
        return f"select public.publicar_anuncio('{animal or self.animal}');"

    def preparar(self):
        return (f"update public.animais set email_contato='teste@example.org' where id='{self.animal}';"
                f"update public.animal_fotos set estado='pronta' where id='{self.foto}';")

    def test_publicacao_valida_e_repeticao(self):
        self.run_case(self.preparar() + f"""
        create temp table antes as select to_jsonb(a)-array['status_publicacao','atualizado_em'] dados
            from public.animais a where id='{self.animal}';
        {claims()} {self.publicar()}
        select pg_temp.check_true((select status_publicacao='publicado' from public.animais where id='{self.animal}'));
        select pg_temp.expect_error({literal(self.publicar())},'42501');
        reset role;
        select pg_temp.check_true((select to_jsonb(a)-array['status_publicacao','atualizado_em']=antes.dados
            from public.animais a,antes where id='{self.animal}'));
        """, self.objeto())

    def test_condicoes_recusadas(self):
        for mudar in [
            "email_contato=null", "status_adocao='adotado'", "oculto_admin=true",
            f"usuario_id='{B}'",
        ]:
            with self.subTest(mudar=mudar):
                self.run_case(self.preparar() + f"update public.animais set {mudar} where id='{self.animal}';"
                              + claims() + f"select pg_temp.expect_error({literal(self.publicar())},'42501');",
                              self.objeto())
        self.run_case(claims() + f"select pg_temp.expect_error({literal(self.publicar(B))},'42501');")
        self.run_case(self.preparar() + f"update auth.users set email_confirmed_at=null where id='{A}';"
                      + claims() + f"select pg_temp.expect_error({literal(self.publicar())},'42501');", self.objeto())

    def test_foto_ausente_pendente_removendo_e_objeto_invalido(self):
        for estado in ['pendente', 'removendo', 'ausente', 'pronta']:
            with self.subTest(estado=estado):
                foto = (f"delete from public.animal_fotos where id='{self.foto}';" if estado == 'ausente'
                        else '' if estado == 'pendente'
                        else f"update public.animal_fotos set estado='{estado}' where id='{self.foto}';")
                self.run_case(f"update public.animais set whatsapp='22999999999' where id='{self.animal}';"
                              + foto + claims() + f"select pg_temp.expect_error({literal(self.publicar())},'42501');")
        for objeto in [self.objeto(owner=B), self.objeto(bucket='outro'), self.objeto(path='forjado.webp')]:
            self.run_case(self.preparar() + claims()
                          + f"select pg_temp.expect_error({literal(self.publicar())},'42501');", objeto)

    def test_autorizacao_e_update_direto(self):
        for contexto in [claims(B), claims(''), 'set local role anon;']:
            self.run_case(self.preparar() + contexto
                          + f"select pg_temp.expect_error({literal(self.publicar())},'42501');", self.objeto())
        self.run_case(self.preparar() + claims() + f"""
        select pg_temp.expect_error('update public.animais set status_publicacao=''publicado'' where id=''{self.animal}''','42501');
        """, self.objeto())
        self.run_case(claims() + "select pg_temp.expect_error('select public.publicar_anuncio(null)','42501');")

    def test_fotografia_publicada_protegida(self):
        self.run_case(self.preparar() + claims() + self.publicar() + f"""
        select pg_temp.expect_error('update public.animal_fotos set estado=''removendo'' where id=''{self.foto}''','42501');
        select pg_temp.check_true((select estado='pronta' from public.animal_fotos where id='{self.foto}'));
        """, self.objeto())

    def test_pendente_publicada_protegida_e_operadores_preservados(self):
        nova = str(uuid.uuid4())
        self.run_case(self.preparar()
                      + f"insert into public.animal_fotos(id,animal_id,posicao) values ('{nova}','{self.animal}',2);"
                      + claims() + self.publicar() + f"""
        select pg_temp.expect_error('update public.animal_fotos set estado=''removendo'' where id=''{nova}''','42501');
        reset role;
        set local role service_role;
        update public.animal_fotos set estado='removendo' where id='{nova}';
        reset role;
        update public.animal_fotos set estado='removendo' where id='{self.foto}';
        select pg_temp.check_true((select count(*)=2 from public.animal_fotos
            where animal_id='{self.animal}' and estado='removendo'));
        """, self.objeto())

    def test_privilegios_e_auxiliar_direta(self):
        self.run_case(f"""
        select pg_temp.check_true(not has_column_privilege('authenticated','public.animais','status_publicacao','UPDATE'));
        select pg_temp.check_true(p.prosecdef=(n.nspname='adotae_private')
            and p.proconfig=array['search_path=""'] and pg_get_userbyid(p.proowner)='postgres')
        from pg_proc p join pg_namespace n on n.oid=p.pronamespace
        where p.oid in ('public.publicar_anuncio(uuid)'::regprocedure,'adotae_private.publicar_anuncio(uuid)'::regprocedure);
        select pg_temp.check_true(not has_function_privilege(r,f,'EXECUTE'))
        from unnest(array['anon','service_role']) r,
             unnest(array['public.publicar_anuncio(uuid)','adotae_private.publicar_anuncio(uuid)']) f;
        select pg_temp.check_true(not exists(select 1 from pg_proc p,
            lateral aclexplode(p.proacl) acl where p.oid in (
                'public.publicar_anuncio(uuid)'::regprocedure,'adotae_private.publicar_anuncio(uuid)'::regprocedure)
            and acl.grantee=0));
        """ + self.preparar() + claims(B) + f"""
        select pg_temp.expect_error('select adotae_private.publicar_anuncio(''{self.animal}'')','42501');
        """ + claims() + f"select adotae_private.publicar_anuncio('{self.animal}');", self.objeto())

    def test_migracao_publicacao_recusa_update_efetivo(self):
        migration = (fotos.ROOT / 'supabase/migrations/202611080002_publicar_anuncio.sql').read_text(encoding='utf-8')
        for grant in ['grant update on public.animais to authenticated;',
                      'grant update(status_publicacao) on public.animais to public;']:
            with self.subTest(grant=grant):
                with self.assertRaisesRegex(AssertionError, 'authenticated mantém UPDATE efetivo'):
                    self.execute('begin;' + grant + migration)
                self.assertEqual(self.execute("select has_column_privilege("
                    "'authenticated','public.animais','status_publicacao','UPDATE');"), 'f')

    def test_claim_falso_nao_confirma_email(self):
        self.run_case(self.preparar() + f"update auth.users set email_confirmed_at=null where id='{A}';"
                      + claims() + "select set_config('request.jwt.claims',"
                      + literal('{"role":"service_role","email_confirmed_at":"2026-11-08"}') + ",true);"
                      + f"select pg_temp.expect_error({literal(self.publicar())},'42501');", self.objeto())

    def test_nova_reserva_apos_conflito_exige_nova_chamada(self):
        nova = str(uuid.uuid4())
        caminho = f'{A}/{self.animal}/{nova}.webp'
        self.execute(self.fixtures + f"update public.animais set whatsapp='22999999999' where id='{self.animal}';")
        try:
            # A publicação não espera segurando outros locks. Após sua recusa,
            # a outra transação cria e confirma uma nova reserva.
            code, error = self.concorrer(
                f"select id from public.animal_fotos where id='{self.foto}' for update;",
                claims() + self.publicar(),
                f"insert into public.animal_fotos(id,animal_id,posicao) values ('{nova}','{self.animal}',2);"
                + self.objeto(path=caminho)
                + claims() + f"select public.confirmar_foto('{nova}');", segunda_pode_recusar=True)
            self.assertNotEqual(code, 0)
            self.assertIn('55P03', error)
            # Uma nova chamada bloqueia ambas e pode publicar com a segunda.
            self.execute('begin;' + claims() + self.publicar() + 'commit;')
            self.assertEqual(self.execute(f"select status_publicacao from public.animais where id='{self.animal}';"), 'publicado')
        finally:
            self.execute(f"delete from storage.objects where name={literal(caminho)};"
                         f"delete from public.animal_fotos where animal_id='{self.animal}';"
                         f"delete from public.animais where id='{self.animal}';")

    def test_confirmacao_pendente_concorrente_com_publicacao(self):
        self.execute(self.fixtures + self.objeto()
                     + f"update public.animais set whatsapp='22999999999' where id='{self.animal}';")
        try:
            code, error = self.concorrer(claims() + self.confirmar(), claims() + self.publicar(), segunda_pode_recusar=True)
            self.assertNotEqual(code, 0)
            self.assertIn('55P03', error)
            self.execute('begin;' + claims() + self.publicar() + 'commit;')
            self.assertEqual(self.execute(f"select status_publicacao from public.animais where id='{self.animal}';"), 'publicado')
        finally:
            self.execute(f"delete from storage.objects where name={literal(self.path)};"
                         f"delete from public.animal_fotos where id='{self.foto}';"
                         f"delete from public.animais where id='{self.animal}';")

    def test_concorrencia_publicacao_fotos(self):
        for operacao in ['remover', 'confirmar', 'publicar']:
            for publica_primeiro in [True, False]:
                with self.subTest(operacao=operacao, publica_primeiro=publica_primeiro):
                    self.execute(self.fixtures + self.objeto() + self.preparar())
                    publicar = claims() + self.publicar()
                    outra = claims() + ({
                        'remover': f"update public.animal_fotos set estado='removendo' where id='{self.foto}';",
                        'confirmar': self.confirmar(), 'publicar': self.publicar(),
                    }[operacao])
                    try:
                        code, error = (self.concorrer(publicar, outra, segunda_pode_recusar=operacao == 'publicar')
                                       if publica_primeiro else self.concorrer(outra, publicar, segunda_pode_recusar=True))
                        self.assertNotEqual(code, 0)
                        self.assertIn('42501' if publica_primeiro and operacao != 'publicar' else '55P03', error)
                        if not publica_primeiro and operacao == 'confirmar':
                            self.execute('begin;' + claims() + self.publicar() + 'commit;')
                        self.assertEqual(self.execute(f"select status_publicacao from public.animais where id='{self.animal}';"),
                                         'rascunho' if operacao == 'remover' and not publica_primeiro else 'publicado')
                    finally:
                        self.execute(f"delete from storage.objects where name={literal(self.path)};"
                                     f"delete from public.animal_fotos where id='{self.foto}';"
                                     f"delete from public.animais where id='{self.animal}';")
                        self.setUp()

    def test_remocao_multiplas_fotos_ordem_inversa_sem_deadlock(self):
        menor = '00000000-0000-4000-8000-000000000001'
        maior = 'ffffffff-ffff-4fff-8fff-ffffffffffff'
        self.foto = menor
        self.path = f'{A}/{self.animal}/{menor}.webp'
        # Ordem física inversa à ordem de UUID da publicação.
        self.fixtures = self.fixtures[:self.fixtures.index('insert into public.animal_fotos')]
        self.execute(self.fixtures
                     + f"insert into public.animal_fotos(id,animal_id,posicao) values ('{maior}','{self.animal}',2),('{menor}','{self.animal}',1);"
                     + self.objeto() + self.preparar())
        try:
            # Reproduz uma remoção que adquiriu a segunda foto antes da primeira.
            code, error = self.concorrer(
                claims() + f"update public.animal_fotos set estado='removendo' where id='{maior}';",
                claims() + self.publicar(),
                f"update public.animal_fotos set estado='removendo' where animal_id='{self.animal}';",
                segunda_pode_recusar=True)
            self.assertNotEqual(code, 0)
            self.assertIn('55P03', error)
            self.assertEqual(self.execute(f"select count(*) from public.animal_fotos where animal_id='{self.animal}' and estado='removendo';"), '2')
        finally:
            self.execute(f"delete from storage.objects where name={literal(self.path)};"
                         f"delete from public.animal_fotos where animal_id='{self.animal}';"
                         f"delete from public.animais where id='{self.animal}';")


if __name__ == '__main__':
    unittest.main(verbosity=2)
