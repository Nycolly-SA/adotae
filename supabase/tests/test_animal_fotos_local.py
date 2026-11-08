"""Integração SQL em PostgreSQL LOCAL descartável, sem rede externa/dependências.

Execução explícita: ADOTAE_TEST_LOCAL_PG=1 e servidor em 127.0.0.1:55439 cujo
data_directory se chame adotae-fotos-pg-*. Cria/remove apenas um banco aleatório.
storage.objects abaixo é um STUB local: suas fixtures não representam uploads
nem devem ser executadas contra o schema Storage de um projeto Supabase.
"""

import json
import os
from pathlib import Path
import shutil
import subprocess
import time
import unittest
import uuid


ROOT = Path(__file__).resolve().parents[2]
PG_BIN = Path(os.environ.get("ProgramFiles", "C:/Program Files")) / "PostgreSQL/18/bin"
PSQL = shutil.which("psql") or str(PG_BIN / "psql.exe")
A = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
B = "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"
LOCAL_ENV = {key: value for key, value in os.environ.items() if not key.upper().startswith("PG")}
LOCAL_ENV.update(PGCLIENTENCODING="UTF8", PGCONNECT_TIMEOUT="5")


def literal(value):
    return "'" + str(value).replace("'", "''") + "'"


def claims(user=A):
    identity = {"role": "authenticated"}
    if user:
        identity["sub"] = user
    return (
        f"select set_config('request.jwt.claim.sub',{literal(user)},true);"
        f"select set_config('request.jwt.claims',{literal(json.dumps(identity))},true);"
        "set local role authenticated;"
    )


HELPERS = """
create function pg_temp.check_true(ok boolean) returns void language plpgsql as $$
begin
    if ok is distinct from true then raise exception 'Asserção falhou'; end if;
end; $$;
create function pg_temp.expect_error(sql text, expected text) returns void language plpgsql as $$
begin
    begin execute sql;
    exception when others then
        if sqlstate = expected then return; end if;
        raise;
    end;
    raise exception 'Esperado erro %, mas comando foi aceito', expected;
end; $$;
"""

BOOTSTRAP = f"""
create schema auth;
create schema storage;
grant usage on schema auth,storage to anon,authenticated,service_role;
create table auth.users(id uuid primary key, email_confirmed_at timestamptz);
insert into auth.users values ('{A}',now()),('{B}',now());
create function auth.uid() returns uuid language sql stable as $$
select coalesce(nullif(current_setting('request.jwt.claim.sub',true),''),
nullif(current_setting('request.jwt.claims',true),'')::jsonb->>'sub')::uuid;
$$;
-- SIMULAÇÃO: tabela local, sem buckets, API HTTP ou armazenamento de bytes.
create table storage.objects(
    id uuid default gen_random_uuid(), bucket_id text not null, name text not null,
    owner_id text, metadata jsonb, unique(bucket_id,name)
);
alter table storage.objects enable row level security;
grant all on storage.objects to anon,authenticated,service_role;
create function storage.operation() returns text language sql stable as $$
select current_setting('storage.operation',true); $$;
create function storage.allow_only_operation(expected text) returns boolean language sql stable as $$
select coalesce(regexp_replace(storage.operation(),'^storage[.]','') =
nullif(regexp_replace(expected,'^storage[.]',''),''),false); $$;
create function storage.allow_any_operation(expected text[]) returns boolean language sql stable as $$
select exists(select 1 from unnest(expected) op where storage.allow_only_operation(op)); $$;
-- Simular defaults amplos para exercitar as revogações da migração.
alter default privileges in schema public grant all on tables to anon,authenticated,service_role;
alter default privileges grant execute on functions to anon,authenticated,service_role;
"""


@unittest.skipUnless(os.environ.get("ADOTAE_TEST_LOCAL_PG") == "1", "requer PostgreSQL local explícito")
class FotosSQLLocal(unittest.TestCase):
    @classmethod
    def command(cls, database=None):
        return [PSQL, "-X", "-h", "127.0.0.1", "-p", "55439", "-U", "postgres",
                "-d", database or cls.database, "-At", "-v", "ON_ERROR_STOP=1",
                "-v", "VERBOSITY=verbose"]

    @classmethod
    def execute(cls, sql, database=None):
        result = subprocess.run(cls.command(database), input=sql, encoding="utf-8",
                                capture_output=True, timeout=30,
                                env=LOCAL_ENV)
        if result.returncode:
            raise AssertionError(result.stderr)
        return result.stdout.strip()

    @classmethod
    def setUpClass(cls):
        cls.database = "adotae_fotos_test_" + uuid.uuid4().hex
        directory = cls.execute("show data_directory;", database="postgres")
        if not Path(directory).name.startswith("adotae-fotos-pg-"):
            raise RuntimeError("Servidor deve ser a instância temporária adotae-fotos-pg-*")
        cls.execute(f"create database {cls.database};", database="postgres")
        cls.addClassCleanup(cls.execute, f"drop database {cls.database};", database="postgres")
        # Papéis globais somente se faltarem no cluster LOCAL isolado.
        cls.execute("""do $$ begin
        if not exists(select 1 from pg_roles where rolname='anon') then create role anon; end if;
        if not exists(select 1 from pg_roles where rolname='authenticated') then create role authenticated; end if;
        if not exists(select 1 from pg_roles where rolname='service_role') then create role service_role bypassrls; end if;
        end; $$;""")
        cls.execute(BOOTSTRAP)
        cls.execute((ROOT / "supabase/migrations/202611010001_create_animais.sql").read_text(encoding="utf-8"))
        cls.before = cls.audit()
        cls.execute((ROOT / "supabase/migrations/202611060001_create_animal_fotos.sql").read_text(encoding="utf-8"))
        cls.storage_before = cls.storage_policy_metadata()
        cls.execute((ROOT / "supabase/migrations/202611070001_fix_animal_fotos_storage_leitura.sql").read_text(encoding="utf-8"))
        cls.fotos_before = cls.fotos_metadata()
        cls.execute((ROOT / "supabase/migrations/202611080001_protect_animais_edicao.sql").read_text(encoding="utf-8"))
        cls.execute((ROOT / "supabase/migrations/202611080002_publicar_anuncio.sql").read_text(encoding="utf-8"))

    @classmethod
    def fotos_metadata(cls):
        return cls.execute("""select jsonb_build_object(
        'tabela',(select to_jsonb(c) from pg_class c where oid='public.animal_fotos'::regclass),
        'colunas',(select jsonb_agg(to_jsonb(a) order by attnum) from pg_attribute a
            where attrelid='public.animal_fotos'::regclass),
        'politicas',(select jsonb_agg(to_jsonb(p) order by schemaname,tablename,policyname)
            from pg_policies p where (schemaname,tablename) in
            (('public','animal_fotos'),('storage','objects'))));""")

    @classmethod
    def storage_policy_metadata(cls):
        # Só o USING da política de leitura pode mudar na migração corretiva.
        return cls.execute("""select jsonb_agg(case
        when policyname='animal_fotos_storage_leitura' then to_jsonb(p)-'qual'
        else to_jsonb(p) end order by policyname)
        from pg_policies p where schemaname='storage' and tablename='objects';""")

    @classmethod
    def audit(cls):
        return cls.execute("""select jsonb_build_object('animais_acl',relacl::text,
        'politicas',(select jsonb_agg(to_jsonb(p) order by policyname) from pg_policies p
        where schemaname='public' and tablename='animais'),
        'storage_acl',(select relacl::text from pg_class where oid='storage.objects'::regclass))
        from pg_class where oid='public.animais'::regclass;""")

    def setUp(self):
        self.animal, self.foto = str(uuid.uuid4()), str(uuid.uuid4())
        self.path = f"{A}/{self.animal}/{self.foto}.webp"
        self.fixtures = f"""
        insert into public.animais(id,usuario_id,nome,especie,sexo,idade_aproximada,porte,descricao,distrito)
        values ('{self.animal}','{A}','Fixture RPC','Gato','Macho','1 ano','Pequeno','Teste local','Atafona');
        insert into public.animal_fotos(id,animal_id,posicao) values ('{self.foto}','{self.animal}',1);
        """

    def objeto(self, *, path=None, owner=A, bucket="animal-fotos", metadata=None):
        if metadata is None:
            metadata = {"size": 512000, "mimetype": "image/webp"}
        return (
            "insert into storage.objects(bucket_id,name,owner_id,metadata) values ("
            f"{literal(bucket)},{literal(path or self.path)},{literal(owner)},"
            f"{literal(json.dumps(metadata))}::jsonb);"
        )

    def confirmar(self):
        return f"select public.confirmar_foto('{self.foto}');"

    def erro(self, codigo):
        return f"select pg_temp.expect_error({literal(self.confirmar())},'{codigo}');"

    def run_case(self, body, objeto=""):
        self.execute("begin;" + HELPERS + self.fixtures + objeto + body + "rollback;")

    def test_roteiro_rls_e_privilegios_preservados(self):
        self.execute((ROOT / "supabase/tests/animal_fotos_rls.sql").read_text(encoding="utf-8"))
        self.assertEqual(self.before, self.audit())
        self.assertEqual(self.storage_before, self.storage_policy_metadata())
        self.assertEqual(self.fotos_before, self.fotos_metadata())

    def test_edicao_animais(self):
        self.execute((ROOT / "supabase/tests/animais_edicao.sql").read_text(encoding="utf-8"))

    def test_migracao_edicao_recusa_privilegios_efetivos_amplos(self):
        migration = (ROOT / "supabase/migrations/202611080001_protect_animais_edicao.sql").read_text(encoding="utf-8")
        inherited = "edicao_test_" + uuid.uuid4().hex
        for grant in [
            "grant update on public.animais to authenticated;",
            "grant update(status_adocao) on public.animais to public;",
            f"create role {inherited}; grant {inherited} to authenticated;"
            f"grant update(status_adocao) on public.animais to {inherited};",
        ]:
            with self.subTest(grant=grant):
                # A falha fecha a conexão e desfaz inclusive papel/membership globais.
                with self.assertRaisesRegex(AssertionError, "authenticated mantém UPDATE efetivo"):
                    self.execute("begin;" + grant + migration)
                self.assertEqual(self.execute("select has_column_privilege("
                    "'authenticated','public.animais','status_adocao','UPDATE');"), "f")
                self.assertEqual(self.before, self.audit())

    def test_confirma_e_repete_sem_update_ou_campos_extras(self):
        self.run_case(f"""
        create temp table antes as select to_jsonb(f) - 'estado' campos from public.animal_fotos f;
        {claims()} {self.confirmar()}
        reset role;
        create temp table confirmada as select xmin::text versao,ctid::text localizacao from public.animal_fotos where id='{self.foto}';
        {claims()} {self.confirmar()}
        reset role;
        select pg_temp.check_true((select estado='pronta' and to_jsonb(f)-'estado'=a.campos
            and f.xmin::text=c.versao and f.ctid::text=c.localizacao
            from public.animal_fotos f,antes a,confirmada c where id='{self.foto}'));
        select pg_temp.check_true((select status_publicacao='rascunho' from public.animais where id='{self.animal}'));
        """, self.objeto())

    def test_sem_objeto_ou_objeto_divergente(self):
        cases = ["", self.objeto(path="caminho/forjado.webp"), self.objeto(owner=B),
                 self.objeto(bucket="outro-bucket"),
                 self.objeto(path=self.path.replace(self.animal, str(uuid.uuid4()))),
                 self.objeto(path=self.path.replace(self.foto, str(uuid.uuid4())))]
        for objeto in cases:
            with self.subTest(objeto=objeto):
                self.run_case(claims() + self.erro("P0001"), objeto)

    def test_outro_usuario_anon_e_sem_identidade(self):
        for context in [claims(B), "set local role anon;", claims("")]:
            with self.subTest(context=context):
                self.run_case(context + self.erro("42501"), self.objeto())

    def test_removendo_e_publicado_recusados(self):
        self.run_case(claims() + f"update public.animal_fotos set estado='removendo' where id='{self.foto}';"
                      + self.erro("42501"), self.objeto())
        self.run_case(f"update public.animais set email_contato='teste@example.org',status_publicacao='publicado' where id='{self.animal}';"
                      + claims() + self.erro("42501"), self.objeto())

    def test_metadados_presentes_incompativeis_recusados(self):
        for metadata in [{"size": 512001}, {"size": 0}, {"size": -1}, {"size": 1.5},
                         {"size": "invalido"}, {"size": {}}, {"mimetype": "image/jpeg"}]:
            with self.subTest(metadata=metadata):
                self.run_case(claims() + self.erro("22023"), self.objeto(metadata=metadata))

    def test_metadados_ausentes_ou_parciais_nao_atestam_bytes(self):
        for metadata in [{}, {"size": None, "mimetype": None}, {"size": 1}, {"mimetype": "image/webp"}]:
            with self.subTest(metadata=metadata):
                self.run_case(claims() + self.confirmar(), self.objeto(metadata=metadata))

    def test_auxiliar_direta_tem_mesmas_verificacoes(self):
        chamada = f"select adotae_private.confirmar_foto('{self.foto}');"
        self.run_case(claims(B) + f"select pg_temp.expect_error({literal(chamada)},'42501');", self.objeto())
        self.run_case(claims() + chamada, self.objeto())

    def test_repeticao_revalida_objeto(self):
        self.run_case(f"update public.animal_fotos set estado='pronta' where id='{self.foto}';"
                      + claims() + self.erro("P0001"))

    def concorrer(self, primeira, segunda, antes_commit="", segunda_pode_recusar=False):
        # Duas conexões de trabalho; a conexão observadora só consulta atividade.
        # A primeira mantém a transação aberta até comprovar que bloqueia a segunda.
        application = "fotos_lock_" + uuid.uuid4().hex
        second = None
        first = subprocess.Popen(self.command(), stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                 stderr=subprocess.PIPE, encoding="utf-8", env=LOCAL_ENV)
        try:
            first.stdin.write("begin isolation level read committed;" + primeira + "\n\\echo LOCK_OBTIDO\n")
            first.stdin.flush()
            while True:
                line = first.stdout.readline()
                if "LOCK_OBTIDO" in line:
                    break
                if not line:
                    self.fail(first.stderr.read())
            second = subprocess.Popen(self.command(), stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                      stderr=subprocess.PIPE, encoding="utf-8", env=LOCAL_ENV)
            second.stdin.write(f"set application_name='{application}'; begin isolation level read committed;"
                               + segunda + "commit;\n")
            second.stdin.flush()
            deadline = time.monotonic() + 10
            while self.execute(f"select count(*) from pg_stat_activity where application_name='{application}' and wait_event_type='Lock';") != "1":
                if segunda_pode_recusar and second.poll() is not None:
                    break
                if time.monotonic() > deadline:
                    self.fail("Segunda conexão não aguardou o bloqueio concorrente")
                time.sleep(0.05)
            _, first_err = first.communicate(antes_commit + "commit;\n", timeout=10)
            self.assertEqual(first.returncode, 0, first_err)
            _, second_err = second.communicate(timeout=10)
            self.assertNotIn("40P01", second_err, "Deadlock entre as operações")
            return second.returncode, second_err
        finally:
            for process in [first, second]:
                if process is not None and process.poll() is None:
                    process.kill()
                    process.communicate()

    def test_concorrencia_confirmacao_remocao(self):
        for primeira in ["confirmar", "remover"]:
            with self.subTest(primeira=primeira):
                self.execute(self.fixtures + self.objeto())
                confirmar = claims() + self.confirmar()
                remover = claims() + f"update public.animal_fotos set estado='removendo' where id='{self.foto}';"
                try:
                    code, error = self.concorrer(confirmar, remover) if primeira == "confirmar" else self.concorrer(remover, confirmar)
                    if primeira == "confirmar":
                        self.assertEqual(code, 0, error)
                    else:
                        self.assertNotEqual(code, 0)
                        self.assertIn("42501", error)
                    self.assertEqual(self.execute(f"select estado from public.animal_fotos where id='{self.foto}';"), "removendo")
                finally:
                    self.execute(f"delete from public.animal_fotos where id='{self.foto}'; delete from public.animais where id='{self.animal}';")
                    self.setUp()

    def test_concorrencia_confirmacao_alteracao_animal(self):
        # SHARE protege também colunas não chave: KEY SHARE seria insuficiente
        # contra alteração de status_publicacao. Propriedade é revalidada após espera.
        # A FK da reserva usa KEY SHARE, compatível com SHARE. A solicitação de
        # remoção bloqueia a foto e depois o animal com SHARE (testada acima).
        # Os UPDATEs de campos protegidos são fixtures do operador postgres LOCAL;
        # não implementam publicação/transferência nem concedem esses direitos ao usuário.
        for campo in ["status_publicacao", "usuario_id"]:
            for primeira in ["confirmar", "alterar_animal"]:
                with self.subTest(campo=campo, primeira=primeira):
                    self.execute(self.fixtures + self.objeto())
                    confirmar = claims() + self.confirmar()
                    valor = "publicado" if campo == "status_publicacao" else B
                    alterar = (f"update public.animais set {campo}={literal(valor)},"
                               f"email_contato='teste@example.org' where id='{self.animal}';")
                    try:
                        if primeira == "confirmar":
                            code, error = self.concorrer(confirmar, alterar)
                            self.assertEqual(code, 0, error)
                            esperado = "pronta"
                        else:
                            code, error = self.concorrer(alterar, confirmar)
                            self.assertNotEqual(code, 0)
                            self.assertIn("42501", error)
                            esperado = "pendente"
                        self.assertEqual(self.execute(f"select estado from public.animal_fotos where id='{self.foto}';"), esperado)
                        self.assertEqual(self.execute(f"select {campo} from public.animais where id='{self.animal}';"), valor)
                    finally:
                        self.execute(f"delete from public.animal_fotos where id='{self.foto}'; delete from public.animais where id='{self.animal}';")
                        self.setUp()


if __name__ == "__main__":
    unittest.main(verbosity=2)
