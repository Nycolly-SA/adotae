-- Automatizado pelo ambiente PostgreSQL LOCAL descartável; todas as fixtures
-- e ampliações temporárias de grants são desfeitas por ROLLBACK.
begin;

create function pg_temp.edicao_assert(ok boolean, mensagem text)
returns void language plpgsql as $$
begin
    if ok is distinct from true then raise exception '%', mensagem; end if;
end;
$$;
create function pg_temp.edicao_erro(comando text, esperado text)
returns void language plpgsql as $$
begin
    begin execute comando;
    exception when others then
        if sqlstate = esperado then return; end if;
        raise;
    end;
    raise exception 'Esperado erro %: %', esperado, comando;
end;
$$;

insert into public.animais (
    usuario_id, nome, especie, sexo, idade_aproximada, porte, descricao,
    distrito, email_contato, status_publicacao, criado_em, atualizado_em
)
select dono::uuid, nome, 'Gato', 'Macho', '1 ano', 'Pequeno', 'Original',
    'Atafona', 'contato@example.org', estado, '2020-01-01', '2020-01-01'
from (values
    ('aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa', 'Edicao rascunho', 'rascunho'),
    ('aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa', 'Edicao publicado', 'publicado'),
    ('bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb', 'Edicao B', 'publicado')
) v(dono, nome, estado);

select set_config('request.jwt.claim.sub', 'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa', true);
select set_config('request.jwt.claims', '{"role":"authenticated","sub":"aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"}', true);
set local role authenticated;

-- Cada campo permitido de rascunho continua editável, incluindo NULL opcionais.
update public.animais set nome = 'Edicao rascunho alterado', especie = 'Outro animal doméstico',
    especie_outro = 'Coelho', sexo = 'Fêmea', idade_aproximada = '2 anos', porte = 'Médio',
    descricao = 'Alterada', distrito = 'Barcelos', bairro = 'Centro',
    email_contato = null, whatsapp = '5522999999999' where nome = 'Edicao rascunho';
select pg_temp.edicao_assert((select especie_outro = 'Coelho' and sexo = 'Fêmea'
    and idade_aproximada = '2 anos' and porte = 'Médio' and descricao = 'Alterada'
    and distrito = 'Barcelos' and bairro = 'Centro' and email_contato is null
    and whatsapp = '5522999999999' and status_publicacao = 'rascunho'
    from public.animais where nome = 'Edicao rascunho alterado'), 'rascunho editável');
update public.animais set especie = 'Gato', especie_outro = null, bairro = null,
    email_contato = null, whatsapp = null where nome = 'Edicao rascunho alterado';

-- Publicados: rejeitar cada campo individualmente e também UPDATE misto.
select pg_temp.edicao_erro(format(
    'update public.animais set %I = %s where nome = ''Edicao publicado''', campo, valor), '42501')
from (values ('nome', '''Outro nome'''), ('especie', '''Cachorro'''),
    ('especie_outro', '''Coelho'''), ('sexo', '''Fêmea'''), ('idade_aproximada', '''3 anos'''),
    ('porte', '''Grande'''), ('descricao', '''Outra descrição'''),
    ('distrito', '''Barcelos'''), ('bairro', '''Centro'''),
    ('descricao', 'null')) v(campo, valor);
select pg_temp.edicao_erro('update public.animais set email_contato = ''novo@example.org'',
    descricao = ''Mista'' where nome = ''Edicao publicado''', '42501');

with alteradas as (update public.animais set email_contato = 'novo@example.org'
    where nome = 'Edicao publicado' returning *)
select pg_temp.edicao_assert((select email_contato = 'novo@example.org'
    and atualizado_em = statement_timestamp() and atualizado_em > criado_em
    and criado_em = '2020-01-01'::timestamptz
    from alteradas), 'email e data automáticos');
update public.animais set whatsapp = '5522999999999', email_contato = null
    where nome = 'Edicao publicado';
select pg_temp.edicao_assert((select whatsapp = '5522999999999' and email_contato is null
    from public.animais where nome = 'Edicao publicado'), 'troca atômica de contato');
select pg_temp.edicao_erro('update public.animais set whatsapp = null
    where nome = ''Edicao publicado''', '23514');
select pg_temp.edicao_erro('update public.animais set email_contato = ''inválido''
    where nome = ''Edicao publicado''', '23514');
select pg_temp.edicao_erro('update public.animais set whatsapp = ''inválido''
    where nome = ''Edicao publicado''', '23514');
update public.animais set email_contato = 'final@example.org', whatsapp = null
    where nome = 'Edicao publicado';
-- Valores iguais são permitidos; o trigger de data ainda executa.
with alteradas as (update public.animais set nome = nome, bairro = bairro
    where nome = 'Edicao publicado' returning *)
select pg_temp.edicao_assert((select atualizado_em = statement_timestamp()
    from alteradas), 'UPDATE sem mudança atualiza data');
select pg_temp.edicao_assert((select atualizado_em > criado_em
    from public.animais where nome = 'Edicao rascunho alterado'), 'data de rascunho');

-- Privilégios efetivos e SQL direto: status não pode nem ser atribuído a si mesmo.
select pg_temp.edicao_assert(not has_column_privilege('authenticated', 'public.animais', c, 'UPDATE'),
    'coluna protegida sem privilégio efetivo: ' || c)
from unnest(array['id','usuario_id','status_adocao','status_publicacao','oculto_admin','criado_em','atualizado_em']) c;
select pg_temp.edicao_erro(format('update public.animais set %I = %I', c, c), '42501')
from unnest(array['status_adocao','status_publicacao','oculto_admin','atualizado_em']) c;

with alteradas as (update public.animais set email_contato = 'invasao@example.org'
    where nome = 'Edicao B' returning *)
select pg_temp.edicao_assert((select count(*) = 0 from alteradas), 'A não edita B');
select set_config('request.jwt.claim.sub', 'bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb', true);
with alteradas as (update public.animais set email_contato = 'invasao@example.org'
    where nome in ('Edicao publicado', 'Edicao rascunho alterado') returning *)
select pg_temp.edicao_assert((select count(*) = 0 from alteradas), 'B não edita A');

reset role;
-- Defesa adicional do trigger se grants forem ampliados por engano.
savepoint grant_indevido;
grant update on public.animais to authenticated;
select set_config('request.jwt.claim.sub', 'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa', true);
set local role authenticated;
-- Um claim administrativo forjado não muda o papel SQL executor.
select set_config('request.jwt.claims', '{"role":"service_role"}', true);
select pg_temp.edicao_erro(format('update public.animais set %I = %s where nome = %L', c, valor, nome), '42501')
from (values ('id','gen_random_uuid()'), ('usuario_id', '''bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb'''),
    ('status_adocao', '''adotado'''), ('status_publicacao', '''publicado'''),
    ('oculto_admin', 'true'), ('criado_em', 'now()')) v(c, valor)
cross join (values ('Edicao rascunho alterado'), ('Edicao publicado')) n(nome)
where not (c = 'status_publicacao' and nome = 'Edicao publicado');
select pg_temp.edicao_erro('update public.animais set status_publicacao = ''rascunho'',
    descricao = ''Burla'' where nome = ''Edicao publicado''', '42501');
reset role;
rollback to savepoint grant_indevido;
-- ROLLBACK TO também restaura os claims anteriores (usuário B).
select set_config('request.jwt.claim.sub', 'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa', true);
select set_config('request.jwt.claims', '{"role":"authenticated","sub":"aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"}', true);

select pg_temp.edicao_assert((select not prosecdef and proconfig = array['search_path=""']
    from pg_proc where oid = 'public.animais_validar_edicao()'::regprocedure),
    'trigger invoker com search_path seguro');
select pg_temp.edicao_assert(not has_function_privilege(r,
    'public.animais_validar_edicao()', 'EXECUTE'), 'trigger sem RPC: ' || r)
from unnest(array['anon', 'authenticated']) r;

-- Operações administrativas legítimas, com claims comuns ainda presentes.
update public.animais set descricao = 'Correção administrativa', bairro = 'Centro',
    status_adocao = 'adotado', oculto_admin = true where nome = 'Edicao publicado';
select pg_temp.edicao_assert((select descricao = 'Correção administrativa' and bairro = 'Centro'
    and status_adocao = 'adotado' and oculto_admin from public.animais
    where nome = 'Edicao publicado'), 'postgres preserva administração');
set local role authenticated;
update public.animais set email_contato = 'oculto@example.org' where nome = 'Edicao publicado';
select pg_temp.edicao_assert((select oculto_admin and status_adocao = 'adotado'
    and email_contato = 'oculto@example.org' from public.animais
    where nome = 'Edicao publicado'), 'contato permitido em publicado oculto/adotado');
select pg_temp.edicao_erro('update public.animais set bairro = null
    where nome = ''Edicao publicado''', '42501');
reset role;
set local role service_role;
update public.animais set descricao = 'Operador privilegiado', oculto_admin = false,
    status_adocao = 'disponivel' where nome = 'Edicao publicado';
select pg_temp.edicao_assert((select descricao = 'Operador privilegiado' and not oculto_admin
    and status_adocao = 'disponivel' from public.animais
    where nome = 'Edicao publicado'), 'service_role preserva administração');
reset role;
set local role anon;
select pg_temp.edicao_erro('update public.animais set email_contato = ''anon@example.org''
    where nome = ''Edicao publicado''', '42501');
reset role;
rollback;
