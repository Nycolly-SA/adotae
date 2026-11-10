"""RLS real em PostgreSQL descartável; não emite tokens nem acessa Storage HTTP."""

import unittest

import test_animal_fotos_local as fotos
from test_animal_fotos_local import A, B, claims, literal


class FotosPublicasSQLLocal(fotos.FotosSQLLocal):
    # Reexecuta as regressões do proprietário com a migração nova instalada.
    def publico(self):
        return (f"update public.animal_fotos set estado='pronta' where id='{self.foto}';"
                f"update public.animais set email_contato='teste@example.org',"
                f"status_publicacao='publicado' where id='{self.animal}';")

    def novo_caso(self, sql, objeto=None):
        self.execute('begin;' + fotos.HELPERS + self.fixtures
                     + (self.objeto() if objeto is None else objeto)
                     + self.publico() + sql + 'rollback;')

    def verificar_objetos(self, esperado):
        return f"select pg_temp.check_true((select count(*)={esperado} from storage.objects where bucket_id='animal-fotos'));"

    def anon(self, op='object.sign'):
        return ("reset role; set local role anon;"
                f"select set_config('storage.operation',{literal(op)},true);")

    def test_assinatura_publica_e_metadados_minimos(self):
        self.novo_caso(self.anon() + self.verificar_objetos(1) + f"""
        select pg_temp.check_true((select id='{self.foto}' and animal_id='{self.animal}'
            and estado='pronta' from public.animal_fotos));
        select pg_temp.expect_error('select * from public.animal_fotos','42501');
        select pg_temp.expect_error('select posicao from public.animal_fotos','42501');
        select pg_temp.expect_error('select criado_em from public.animal_fotos','42501');
        """)

    def test_estados_privados_e_reservas_nao_prontas(self):
        for alterar in ["status_publicacao='rascunho'", "status_adocao='adotado'", 'oculto_admin=true']:
            with self.subTest(alterar=alterar):
                self.novo_caso(f"update public.animais set {alterar} where id='{self.animal}';"
                    + self.anon() + self.verificar_objetos(0)
                    + 'select pg_temp.check_true(not exists(select id from public.animal_fotos));')
        for estado in ['pendente', 'removendo', 'ausente']:
            with self.subTest(estado=estado):
                if estado == 'removendo':
                    mudar = f"update public.animal_fotos set estado='removendo' where id='{self.foto}';"
                else:
                    mudar = f"delete from public.animal_fotos where id='{self.foto}';"
                    if estado == 'pendente':
                        mudar += ("insert into public.animal_fotos(id,animal_id,posicao) "
                                  f"values ('{self.foto}','{self.animal}',1);")
                self.novo_caso(mudar + self.anon() + self.verificar_objetos(0)
                    + 'select pg_temp.check_true(not exists(select id from public.animal_fotos));')

    def test_vinculo_exato_do_objeto(self):
        for objeto in ['', self.objeto(owner=B), self.objeto(owner=''), self.objeto(bucket='outro'),
                       self.objeto(path=self.path.upper()), self.objeto(path=self.path + '/extra'),
                       self.objeto(path=self.path.replace(A, B)),
                       self.objeto(path=self.path.replace('.webp', '.jpg'))]:
            with self.subTest(objeto=objeto):
                self.novo_caso(self.anon() + self.verificar_objetos(0), objeto)
        self.novo_caso(f"update public.animais set usuario_id='{B}' where id='{self.animal}';"
                      + self.anon() + self.verificar_objetos(0))

    def test_operacoes_alternativas_e_sql_sem_contexto(self):
        for op in ['', 'object.list', 'object.list_v2', 'object.get_authenticated',
                   'object.get_authenticated_info', 'object.sign_many', 'object.sign_upload_url',
                   'object.copy', 'object.move', 'object.upload', 'object.upload_signed',
                   'object.upload_update', 'object.delete', 'object.delete_many', 'object.get_public',
                   'object.get_signed', 'render.image_authenticated', 's3.object.get']:
            with self.subTest(op=op):
                self.novo_caso(self.anon(op) + self.verificar_objetos(0))
        self.novo_caso('set local role anon;' + self.verificar_objetos(0))

    def test_politicas_permissivas_nao_contornam_guardas(self):
        amplo = """
        create policy teste_ampla on storage.objects for all to anon using(true) with check(true);
        create policy teste_fotos_ampla on public.animal_fotos for select to anon using(true);
        create policy teste_animais_ampla on public.animais for select to anon using(true);
        """
        for op in ['object.sign', 'object.list', 'object.copy', 'object.upload', 'object.delete']:
            with self.subTest(op=op):
                self.novo_caso(amplo + self.anon(op) + f"""
                select pg_temp.expect_error('insert into storage.objects(bucket_id,name) values (''animal-fotos'',''forjado'')','42501');
                with alteradas as (update storage.objects set bucket_id='outro' returning *)
                select pg_temp.check_true((select count(*)=0 from alteradas));
                with apagadas as (delete from storage.objects where bucket_id='animal-fotos' returning *)
                select pg_temp.check_true((select count(*)=0 from apagadas));
                """ + self.verificar_objetos(1 if op == 'object.sign' else 0))
        self.novo_caso(amplo + f"update public.animais set oculto_admin=true where id='{self.animal}';"
                      + self.anon() + self.verificar_objetos(0)
                      + 'select pg_temp.check_true(not exists(select id from public.animal_fotos));')
        self.novo_caso(amplo + "insert into storage.objects(bucket_id,name) values ('outro','teste');"
                      + self.anon('object.list')
                      + "select pg_temp.check_true((select count(*)=1 from storage.objects where bucket_id='outro'));")

    def test_proprietario_e_grants_storage_preservados(self):
        self.novo_caso(claims() + "select set_config('storage.operation','object.get_authenticated',true);"
                      + self.verificar_objetos(1)
                      + "select set_config('storage.operation','object.sign',true);"
                      + self.verificar_objetos(0))
        self.novo_caso(f"update public.animais set status_publicacao='rascunho' where id='{self.animal}';"
                      f"delete from public.animal_fotos where id='{self.foto}';"
                      f"insert into public.animal_fotos(id,animal_id,posicao) values ('{self.foto}','{self.animal}',1);"
                      'delete from storage.objects;' + claims()
                      + "select set_config('storage.operation','object.upload',true);" + self.objeto())


if __name__ == '__main__':
    unittest.main()
