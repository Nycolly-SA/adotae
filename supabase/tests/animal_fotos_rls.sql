-- PENDENTE no Supabase. Executar somente após revisão/autorização, em ambiente
-- descartável, na mesma conexão privilegiada, com as migrações de animais,
-- fotografias, correção de leitura, 202611080002_publicar_anuncio.sql e
-- 202611080003_fotos_publicas_assinatura.sql aplicadas.
-- Como animais_rls.sql: BEGIN, fixtures, SET LOCAL ROLE/claims e ROLLBACK.
-- Este roteiro acrescenta asserções: qualquer divergência gera erro; interromper
-- e executar ROLLBACK. Não executar com service_role como se fosse cliente.
-- Substituir A/B por dois usuários Auth existentes e distintos. Não criar users.
begin;
select set_config('adotae.teste_usuario_a', 'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa', true);
select set_config('adotae.teste_usuario_b', 'bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb', true);

create function pg_temp.fotos_assert(ok boolean, caso text)
returns void language plpgsql security invoker as $$
begin
    if ok is distinct from true then
        raise exception 'Falhou: %', caso;
    end if;
end;
$$;

create function pg_temp.fotos_erro(comando text, codigo text)
returns void language plpgsql security invoker as $$
begin
    begin
        execute comando;
    exception when others then
        if sqlstate = codigo then return; end if;
        raise;
    end;
    raise exception 'Esperado SQLSTATE %, mas comando aceito: %', codigo, comando;
end;
$$;

select pg_temp.fotos_assert(
    current_setting('adotae.teste_usuario_a') <> current_setting('adotae.teste_usuario_b')
    and (select count(*) = 2 from auth.users where id in (
        current_setting('adotae.teste_usuario_a')::uuid,
        current_setting('adotae.teste_usuario_b')::uuid
    )), 'dois usuários Auth distintos existentes');

-- Privilégios EFETIVOS, inclusive herança e grants de tabela que anulam os de
-- coluna. Rodar novamente após qualquer alteração da exposição pela Data API.
select pg_temp.fotos_assert(not has_table_privilege('anon', 'public.animal_fotos', p),
                           'anon sem ' || p)
from unnest(array['SELECT','INSERT','UPDATE','DELETE','TRUNCATE','REFERENCES','TRIGGER']) p;
select pg_temp.fotos_assert(not has_any_column_privilege('anon', 'public.animal_fotos', p),
                           'anon sem privilégios de coluna: ' || p)
from unnest(array['INSERT','UPDATE','REFERENCES']) p;
-- Exceção necessária: apenas metadados usados pela assinatura pública.
select pg_temp.fotos_assert(has_column_privilege('anon','public.animal_fotos',c,'SELECT')
    = (c in ('id','animal_id','estado')), 'SELECT mínimo: ' || c)
from unnest(array['id','animal_id','estado','posicao','criado_em']) c;
select pg_temp.fotos_assert(
    has_table_privilege('authenticated', 'public.animal_fotos', 'SELECT')
    and not has_table_privilege('authenticated', 'public.animal_fotos', 'INSERT')
    and not has_table_privilege('authenticated', 'public.animal_fotos', 'UPDATE'),
    'SELECT de tabela; escrita somente por coluna');
select pg_temp.fotos_assert(not has_table_privilege('authenticated', 'public.animal_fotos', p),
                           'authenticated sem ' || p)
from unnest(array['DELETE','TRUNCATE','REFERENCES','TRIGGER']) p;
select pg_temp.fotos_assert(
    has_column_privilege('authenticated', 'public.animal_fotos', c, 'INSERT')
        = (c in ('animal_id','posicao'))
    and has_column_privilege('authenticated', 'public.animal_fotos', c, 'UPDATE')
        = (c = 'estado')
    and not has_column_privilege('authenticated', 'public.animal_fotos', c, 'REFERENCES'),
    'grants exatos: ' || c)
from unnest(array['id','animal_id','posicao','estado','criado_em']) c;
select pg_temp.fotos_assert(
    (select relrowsecurity from pg_class where oid = 'public.animal_fotos'::regclass)
    and (select relrowsecurity from pg_class where oid = 'storage.objects'::regclass),
    'RLS habilitada nas duas tabelas');
select pg_temp.fotos_assert(not has_function_privilege(
    r, 'public.animal_fotos_validar_alteracao()', 'EXECUTE'), 'trigger sem RPC: ' || r)
from unnest(array['anon','authenticated']) r;
-- RPC: a entrada exposta é invoker; a operação privilegiada fica fora da API.
select pg_temp.fotos_assert(has_function_privilege('authenticated', assinatura, 'EXECUTE')
    and not has_function_privilege('anon', assinatura, 'EXECUTE')
    and not has_function_privilege('service_role', assinatura, 'EXECUTE'), 'EXECUTE restrito: ' || assinatura)
from unnest(array['public.confirmar_foto(uuid)', 'adotae_private.confirmar_foto(uuid)']) assinatura;
select pg_temp.fotos_assert(
    has_schema_privilege('authenticated', 'adotae_private', 'USAGE')
    and not has_schema_privilege('authenticated', 'adotae_private', 'CREATE')
    and not has_schema_privilege('anon', 'adotae_private', 'USAGE')
    and not has_schema_privilege('anon', 'adotae_private', 'CREATE')
    and has_schema_privilege('authenticated', 'public', 'USAGE')
    and not has_schema_privilege('authenticated', 'public', 'CREATE'), 'schemas com grants mínimos');
select pg_temp.fotos_assert(not exists (
    select 1 from pg_proc p cross join lateral aclexplode(coalesce(p.proacl, acldefault('f',p.proowner))) acl
    where p.oid in ('public.confirmar_foto(uuid)'::regprocedure,
                   'adotae_private.confirmar_foto(uuid)'::regprocedure)
      and acl.grantee = 0), 'sem EXECUTE herdado de PUBLIC');
select pg_temp.fotos_assert(not exists (
    select 1 from pg_namespace n cross join lateral aclexplode(coalesce(n.nspacl, acldefault('n',n.nspowner))) acl
    where n.nspname = 'adotae_private' and acl.grantee = 0), 'schema privado sem PUBLIC');
select pg_temp.fotos_assert(p.prosecdef = (n.nspname = 'adotae_private')
    and p.proconfig @> array['search_path=""']
    and pg_get_userbyid(p.proowner) = 'postgres', 'modo, proprietário e search_path: ' || n.nspname)
from pg_proc p join pg_namespace n on n.oid = p.pronamespace
where p.oid in ('public.confirmar_foto(uuid)'::regprocedure,
                'adotae_private.confirmar_foto(uuid)'::regprocedure);
-- Pode estar ausente no SQL Editor: a configuração efetiva da Data API exige
-- conferência adicional. Nunca adicionar adotae_private a Exposed schemas.
select pg_temp.fotos_assert(not ('adotae_private' = any(regexp_split_to_array(
    coalesce(current_setting('pgrst.db_schemas', true), ''), '\s*,\s*'))),
    'schema privado fora da configuração PostgREST visível nesta conexão');
-- Snapshot: nenhuma mudança nas políticas ou ACLs de animais durante os testes.
create temporary table fotos_animais_antes as
select relacl::text acl, (select jsonb_agg(to_jsonb(p) order by policyname)
    from pg_policies p where schemaname = 'public' and tablename = 'animais') politicas
from pg_class where oid = 'public.animais'::regclass;

select set_config('adotae.foto_animal_a', gen_random_uuid()::text, true);
select set_config('adotae.foto_animal_b', gen_random_uuid()::text, true);
select set_config('adotae.foto_publico_a', gen_random_uuid()::text, true);
select set_config('adotae.foto_publico_b', gen_random_uuid()::text, true);
insert into public.animais (
    id, usuario_id, nome, especie, sexo, idade_aproximada, porte,
    descricao, distrito, email_contato, status_publicacao
)
select current_setting(chave)::uuid, current_setting(dono)::uuid,
       'Fixture fotos', 'Gato', 'Fêmea', '1 ano', 'Pequeno',
       'Teste SQL de fotografias', 'Grussaí', 'teste@example.org', estado
from (values
    ('adotae.foto_animal_a', 'adotae.teste_usuario_a', 'rascunho'),
    ('adotae.foto_animal_b', 'adotae.teste_usuario_b', 'rascunho'),
    ('adotae.foto_publico_a', 'adotae.teste_usuario_a', 'publicado'),
    ('adotae.foto_publico_b', 'adotae.teste_usuario_b', 'publicado')
) v(chave, dono, estado);
insert into public.animal_fotos (animal_id, posicao)
select current_setting(chave)::uuid, 1 from (values
    ('adotae.foto_animal_b'), ('adotae.foto_publico_a'), ('adotae.foto_publico_b')
) v(chave);
-- Somente operador confiável confirma prontas; não simula processamento real.
update public.animal_fotos set estado = 'pronta' where animal_id in (
    current_setting('adotae.foto_publico_a')::uuid,
    current_setting('adotae.foto_publico_b')::uuid
);
select set_config('adotae.foto_pronta_a', id::text, true)
from public.animal_fotos where animal_id = current_setting('adotae.foto_publico_a')::uuid;

-- Visitante: somente IDs/estado de prontas públicas; SELECT * segue proibido.
set local role anon;
select set_config('request.jwt.claim.sub', '', true);
select set_config('request.jwt.claims', '{"role":"anon"}', true);
select pg_temp.fotos_erro('select * from public.animal_fotos', '42501');
select pg_temp.fotos_assert((select count(*) = 2 from public.animal_fotos),
    'somente as duas reservas prontas públicas');
select pg_temp.fotos_erro('insert into public.animal_fotos (animal_id,posicao) values (gen_random_uuid(),1)', '42501');
select pg_temp.fotos_erro('update public.animal_fotos set estado = ''removendo''', '42501');
select pg_temp.fotos_erro('delete from public.animal_fotos', '42501');
select pg_temp.fotos_erro('select public.confirmar_foto(gen_random_uuid())', '42501');
select pg_temp.fotos_erro('select adotae_private.confirmar_foto(gen_random_uuid())', '42501');

reset role;
select set_config('request.jwt.claim.sub', current_setting('adotae.teste_usuario_a'), true);
select set_config('request.jwt.claims', json_build_object(
    'sub', current_setting('adotae.teste_usuario_a'), 'role', 'authenticated')::text, true);
set local role authenticated;
select pg_temp.fotos_assert(auth.uid() = current_setting('adotae.teste_usuario_a')::uuid, 'identidade A');
-- A vê o animal público B, mas nunca a fotografia de B.
select pg_temp.fotos_assert(exists(select 1 from public.animais
    where id = current_setting('adotae.foto_publico_b')::uuid), 'animal B público');
select pg_temp.fotos_assert(not exists(select 1 from public.animal_fotos
    where animal_id in (current_setting('adotae.foto_animal_b')::uuid,
                        current_setting('adotae.foto_publico_b')::uuid)), 'fotos B privadas');
insert into public.animal_fotos (animal_id,posicao)
select current_setting('adotae.foto_animal_a')::uuid, n from generate_series(1,3) n;
select pg_temp.fotos_assert((select count(*) = 3 and bool_and(
    estado = 'pendente' and id is not null and criado_em is not null)
    from public.animal_fotos where animal_id = current_setting('adotae.foto_animal_a')::uuid),
    'três reservas com valores gerados');

select pg_temp.fotos_erro(format('insert into public.animal_fotos (animal_id,posicao) values (%L,1)',
    current_setting('adotae.foto_animal_a')), '23505');
select pg_temp.fotos_erro(format('insert into public.animal_fotos (animal_id,posicao) values (%L,%s)',
    current_setting('adotae.foto_animal_a'), n), '23514') from unnest(array[0,4,-1]) n;
select pg_temp.fotos_erro(format('insert into public.animal_fotos (animal_id,posicao) values (%L,null)',
    current_setting('adotae.foto_animal_a')), '23502');
select pg_temp.fotos_erro('insert into public.animal_fotos (animal_id,posicao) values (null,1)', '42501');
select pg_temp.fotos_erro('insert into public.animal_fotos (animal_id,posicao) values (gen_random_uuid(),1)', '42501');
select pg_temp.fotos_erro(format('insert into public.animal_fotos (animal_id,posicao) values (%L,2)',
    current_setting(chave)), '42501') from unnest(array[
        'adotae.foto_animal_b','adotae.foto_publico_b','adotae.foto_publico_a']) chave;
-- Campos omitidos pelo cliente: inclusive estado inicial não é escolhível.
select pg_temp.fotos_erro(format(
    'insert into public.animal_fotos (animal_id,posicao,%I) values (%L,1,%s)', c,
    current_setting('adotae.foto_animal_a'), valor), '42501')
from (values ('id','gen_random_uuid()'), ('criado_em','now()'),
    ('estado',quote_literal('pendente')), ('estado',quote_literal('pronta')),
    ('estado',quote_literal('removendo'))) v(c,valor);
select pg_temp.fotos_erro(format('update public.animal_fotos set %I = %s', c, valor), '42501')
from (values ('id','gen_random_uuid()'), ('animal_id','gen_random_uuid()'),
    ('posicao','2'), ('criado_em','now()')) v(c,valor);
select pg_temp.fotos_erro('delete from public.animal_fotos', '42501');
select pg_temp.fotos_erro('truncate public.animal_fotos', '42501');
select pg_temp.fotos_erro('select public.confirmar_foto(null)', '42501');
select pg_temp.fotos_erro('select public.confirmar_foto(gen_random_uuid())', '42501');
select pg_temp.fotos_erro(format('select public.confirmar_foto(id) from public.animal_fotos where animal_id = %L and posicao = 1',
    current_setting('adotae.foto_animal_a')), 'P0001'); -- reserva sem objeto
select pg_temp.fotos_erro(format('select public.confirmar_foto(%L)',
    current_setting('adotae.foto_pronta_a')), '42501'); -- animal publicado
select pg_temp.fotos_erro('update public.animal_fotos set estado = ''pronta'' where estado = ''pendente''', '42501');
select pg_temp.fotos_erro('update public.animal_fotos set estado = ''pendente'' where estado = ''pronta''', '42501');
select pg_temp.fotos_erro('update public.animal_fotos set estado = ''invalido''', '42501');
select pg_temp.fotos_erro('update public.animal_fotos set estado = null', '42501');
with alteradas as (update public.animal_fotos set estado = 'removendo'
    where animal_id = current_setting('adotae.foto_animal_b')::uuid returning *)
select pg_temp.fotos_assert((select count(*) = 0 from alteradas), 'A não altera B');

-- Avaliação SQL dos predicados instalados, SEM inserir/alterar/excluir metadados
-- de storage.objects. Não comprova a aplicação das políticas pela API Storage.
reset role;
create function pg_temp.fotos_storage(politica text, caminho text, bucket text default 'animal-fotos')
returns boolean language plpgsql security invoker as $$
declare expressao text; permitido boolean;
begin
    select pg_get_expr(coalesce(polwithcheck, polqual), polrelid) into strict expressao
    from pg_policy where polrelid = 'storage.objects'::regclass and polname = politica;
    execute 'select (' || expressao || ') from (select $1::text bucket_id, $2::text name) objects'
        into permitido using bucket, caminho;
    return permitido;
end;
$$;
select set_config('adotae.foto_caminho_pendente',
    current_setting('adotae.teste_usuario_a') || '/' || animal_id || '/' || id || '.webp', true)
from public.animal_fotos where animal_id = current_setting('adotae.foto_animal_a')::uuid and posicao = 1;
select set_config('adotae.foto_caminho_pronta',
    current_setting('adotae.teste_usuario_a') || '/' || current_setting('adotae.foto_publico_a')
    || '/' || current_setting('adotae.foto_pronta_a') || '.webp', true);
set local role authenticated;
select set_config('storage.operation', 'storage.object.upload', true);
select pg_temp.fotos_assert(pg_temp.fotos_storage('animal_fotos_storage_insercao',
    current_setting('adotae.foto_caminho_pendente')), 'upload reservado próprio');
select pg_temp.fotos_assert(not pg_temp.fotos_storage('animal_fotos_storage_insercao', caminho),
    'caminho forjado: ' || caminho) from (values
    (current_setting('adotae.foto_caminho_pronta')),
    (replace(current_setting('adotae.foto_caminho_pendente'), current_setting('adotae.teste_usuario_a'), current_setting('adotae.teste_usuario_b'))),
    (replace(current_setting('adotae.foto_caminho_pendente'), current_setting('adotae.foto_animal_a'), current_setting('adotae.foto_animal_b'))),
    (current_setting('adotae.teste_usuario_a') || '/' || current_setting('adotae.foto_animal_a') || '/' || gen_random_uuid() || '.webp'),
    (current_setting('adotae.foto_caminho_pendente') || '/extra'),
    ('/' || current_setting('adotae.foto_caminho_pendente')),
    (replace(current_setting('adotae.foto_caminho_pendente'), '.webp', '.jpg')),
    (replace(current_setting('adotae.foto_caminho_pendente'), '.webp', '.WEBP')),
    (replace(current_setting('adotae.foto_caminho_pendente'), '/', '//')),
    ('../' || current_setting('adotae.foto_caminho_pendente')),
    ('invalido/sem-uuid/foto.webp')
) v(caminho);
do $$
declare op text; caminho text;
begin
    foreach op in array array['object.get_authenticated', 'object.get_authenticated_info'] loop
        perform set_config('storage.operation', op, true);
        perform pg_temp.fotos_assert(pg_temp.fotos_storage('animal_fotos_storage_leitura',
            current_setting('adotae.foto_caminho_pronta')), 'leitura própria pronta: ' || op);
        perform pg_temp.fotos_assert(not pg_temp.fotos_storage('animal_fotos_storage_leitura',
            current_setting('adotae.foto_caminho_pendente')), 'pendente sem leitura: ' || op);
        perform pg_temp.fotos_assert(not pg_temp.fotos_storage('animal_fotos_storage_exclusao',
            current_setting('adotae.foto_caminho_pronta')), 'leitura não autoriza DELETE: ' || op);
        foreach caminho in array array[
            replace(current_setting('adotae.foto_caminho_pronta'),
                current_setting('adotae.teste_usuario_a'), current_setting('adotae.teste_usuario_b')),
            replace(current_setting('adotae.foto_caminho_pronta'), '.webp', '.jpg'),
            current_setting('adotae.foto_caminho_pronta') || '/extra'
        ] loop
            perform pg_temp.fotos_assert(not pg_temp.fotos_storage('animal_fotos_storage_leitura',
                caminho), 'leitura recusa caminho forjado: ' || op);
        end loop;
    end loop;
end;
$$;
-- Testar isolamento com o MESMO estado que permite a operação para A; negar
-- uma foto em estado incompatível não provaria a verificação de proprietário.
reset role;
select set_config('request.jwt.claim.sub', current_setting('adotae.teste_usuario_b'), true);
select set_config('request.jwt.claims', json_build_object(
    'sub', current_setting('adotae.teste_usuario_b'), 'role', 'authenticated')::text, true);
set local role authenticated;
select pg_temp.fotos_assert(not pg_temp.fotos_storage('animal_fotos_storage_leitura',
    current_setting('adotae.foto_caminho_pronta')), 'B sem leitura de pronta A: ' || op)
from (select op, set_config('storage.operation', op, true) from unnest(array[
    'object.get_authenticated', 'object.get_authenticated_info']) op) operacoes;
select set_config('storage.operation', 'object.upload', true);
select pg_temp.fotos_assert(not pg_temp.fotos_storage('animal_fotos_storage_insercao',
    current_setting('adotae.foto_caminho_pendente')), 'B sem upload na reserva pendente A');
select pg_temp.fotos_assert(not pg_temp.fotos_storage('animal_fotos_storage_insercao',
    replace(current_setting('adotae.foto_caminho_pendente'),
        current_setting('adotae.teste_usuario_a'), current_setting('adotae.teste_usuario_b'))),
    'B não usa reserva A forjando próprio prefixo');
reset role;
select set_config('request.jwt.claim.sub', current_setting('adotae.teste_usuario_a'), true);
select set_config('request.jwt.claims', json_build_object(
    'sub', current_setting('adotae.teste_usuario_a'), 'role', 'authenticated')::text, true);
set local role authenticated;
select set_config('storage.operation', 'object.get_authenticated', true);
-- SELECT não libera assinatura, listagem, cópia, movimento ou rotas alternativas.
select pg_temp.fotos_assert(not pg_temp.fotos_storage('animal_fotos_storage_leitura',
    current_setting('adotae.foto_caminho_pronta')), 'SELECT bloqueado: ' || op)
from (select op, set_config('storage.operation', op, true) from unnest(array[
    '', 'object.sign', 'object.sign_many', 'object.sign_upload_url', 'object.copy',
    'object.move', 'object.list', 'object.list_v2', 'object.get_public',
    'object.head_authenticated_info', 'object.info_public', 'object.info_authenticated',
    'object.get_signed',
    's3.object.get', 'render.image_authenticated']) op) operacoes;
select pg_temp.fotos_assert(not pg_temp.fotos_storage('animal_fotos_storage_insercao',
    current_setting('adotae.foto_caminho_pendente')), 'INSERT bloqueado: ' || op)
from (select op, set_config('storage.operation', op, true) from unnest(array[
    'object.copy','object.move','object.upload_update','object.upload_signed',
    'object.sign_upload_url','s3.upload','tus.upload.create']) op) operacoes;
select pg_temp.fotos_assert(not pg_temp.fotos_storage('animal_fotos_storage_atualizacao',
    current_setting('adotae.foto_caminho_pendente')), 'UPDATE sempre bloqueado');

select set_config('storage.operation', 'object.delete_many', true);
select pg_temp.fotos_assert(not pg_temp.fotos_storage('animal_fotos_storage_exclusao',
    current_setting('adotae.foto_caminho_pronta')), 'pronta não removível antes da solicitação');
select pg_temp.fotos_erro(format('update public.animal_fotos set estado = ''removendo'' where id = %L',
    current_setting('adotae.foto_pronta_a')), '42501');
-- A remoção agora exige rascunho. Somente fixture privilegiada retorna este
-- anúncio ao estado anterior para preservar os testes de remoção/Storage abaixo.
reset role;
update public.animais set status_publicacao = 'rascunho'
where id = current_setting('adotae.foto_publico_a')::uuid;
set local role authenticated;
update public.animal_fotos set estado = 'removendo'
where id = current_setting('adotae.foto_pronta_a')::uuid;
update public.animal_fotos set estado = 'removendo'
where animal_id = current_setting('adotae.foto_animal_a')::uuid and posicao = 1;
select pg_temp.fotos_assert((select count(*) = 2 from public.animal_fotos where estado = 'removendo'),
    'pendente/pronta -> removendo');
select pg_temp.fotos_erro(format('select public.confirmar_foto(%L)',
    current_setting('adotae.foto_pronta_a')), '42501');
select pg_temp.fotos_assert(pg_temp.fotos_storage('animal_fotos_storage_leitura',
    current_setting('adotae.foto_caminho_pronta')) and pg_temp.fotos_storage('animal_fotos_storage_exclusao',
    current_setting('adotae.foto_caminho_pronta')), 'DELETE tem SELECT e DELETE apenas em removendo');
select set_config('storage.operation', 'object.delete', true);
select pg_temp.fotos_assert(pg_temp.fotos_storage('animal_fotos_storage_exclusao',
    current_setting('adotae.foto_caminho_pendente')), 'remoção individual de reserva cancelada');
select set_config('storage.operation', 'object.get_authenticated', true);
select pg_temp.fotos_assert(not pg_temp.fotos_storage('animal_fotos_storage_leitura',
    current_setting('adotae.foto_caminho_pronta')), 'removendo sem leitura: ' || op)
from (select op, set_config('storage.operation', op, true) from unnest(array[
    'object.get_authenticated', 'object.get_authenticated_info']) op) operacoes;
with alteradas as (update public.animal_fotos set estado = 'pendente'
    where estado = 'removendo' returning *)
select pg_temp.fotos_assert((select count(*) = 0 from alteradas), 'removendo terminal para cliente');
-- Antes da remoção lógica, a FK recusa DELETE; depois dela, o grant revogado
-- recusa a própria operação. O roteiro valida ambos os estágios das migrações.
select pg_temp.fotos_erro(format('delete from public.animais where id = %L',
    current_setting('adotae.foto_animal_a')),
    case when has_table_privilege(current_user, 'public.animais', 'DELETE')
        then '23503' else '42501' end);

-- B e authenticated sem sub: isolamento simétrico.
reset role;
select set_config('request.jwt.claim.sub', current_setting('adotae.teste_usuario_b'), true);
select set_config('request.jwt.claims', json_build_object(
    'sub', current_setting('adotae.teste_usuario_b'), 'role', 'authenticated')::text, true);
set local role authenticated;
select pg_temp.fotos_assert((select count(*) = 2 from public.animal_fotos where animal_id in (
    current_setting('adotae.foto_animal_b')::uuid, current_setting('adotae.foto_publico_b')::uuid)), 'B vê próprias');
select pg_temp.fotos_assert(not exists(select 1 from public.animal_fotos where animal_id in (
    current_setting('adotae.foto_animal_a')::uuid, current_setting('adotae.foto_publico_a')::uuid)), 'B não vê A');
select pg_temp.fotos_erro(format('select public.confirmar_foto(%L)',
    current_setting('adotae.foto_pronta_a')), '42501');
select pg_temp.fotos_erro(format('insert into public.animal_fotos (animal_id,posicao) values (%L,2)',
    current_setting('adotae.foto_animal_a')), '42501');
select set_config('storage.operation', 'object.delete_many', true);
select pg_temp.fotos_assert(not pg_temp.fotos_storage('animal_fotos_storage_leitura',
    current_setting('adotae.foto_caminho_pronta')) and not pg_temp.fotos_storage(
        'animal_fotos_storage_exclusao', current_setting('adotae.foto_caminho_pronta')),
    'B sem SELECT auxiliar/DELETE de foto A em removendo');
with alteradas as (update public.animal_fotos set estado = 'removendo'
    where animal_id = current_setting('adotae.foto_animal_a')::uuid returning *)
select pg_temp.fotos_assert((select count(*) = 0 from alteradas), 'B não altera A');
select set_config('request.jwt.claim.sub', '', true);
select set_config('request.jwt.claims', '{"role":"authenticated"}', true);
select pg_temp.fotos_assert(not exists(select 1 from public.animal_fotos), 'sem sub sem fotos');
select pg_temp.fotos_erro('select public.confirmar_foto(gen_random_uuid())', '42501');
select pg_temp.fotos_erro('select adotae_private.confirmar_foto(gen_random_uuid())', '42501');
select pg_temp.fotos_erro(format('insert into public.animal_fotos (animal_id,posicao) values (%L,2)',
    current_setting('adotae.foto_animal_a')), '42501');
reset role;
set local role anon;
-- O helper avalia WITH CHECK quando presente: escrita continua bloqueada.
select pg_temp.fotos_assert(not pg_temp.fotos_storage('animal_fotos_storage_papeis',
    current_setting('adotae.foto_caminho_pronta')), 'anon sem WITH CHECK para escrita');
select pg_temp.fotos_assert(pg_temp.fotos_storage('animal_fotos_storage_papeis',
    'arquivo.webp', 'outro-bucket'), 'guarda neutro para outro bucket');
-- Regressão: mesmo com uma política permissiva ampla, anon consulta outro bucket
-- sem ganhar acesso às fotos fora da assinatura individual de prontas públicas.
reset role;
create policy teste_fotos_outro_bucket on storage.objects for select to anon using (true);
set local role anon;
select pg_temp.fotos_assert(not exists(select 1 from storage.objects
    where bucket_id = 'animal-fotos'), 'anon bloqueado apesar da política permissiva');
select count(*) from storage.objects where bucket_id = 'outro-bucket'; -- sem erro
reset role;
drop policy teste_fotos_outro_bucket on storage.objects;

reset role;
-- Trigger também protege campos/transições se um grant for ampliado por engano.
select pg_temp.fotos_erro('update public.animal_fotos set id = gen_random_uuid()', '42501');
select pg_temp.fotos_erro('update public.animal_fotos set animal_id = gen_random_uuid()', '42501');
select pg_temp.fotos_erro('update public.animal_fotos set posicao = 3 where posicao = 1', '42501');
select pg_temp.fotos_erro('update public.animal_fotos set criado_em = now() + interval ''1 day''', '42501');
select pg_temp.fotos_erro('update public.animal_fotos set estado = ''pendente'' where estado = ''removendo''', '42501');
select pg_temp.fotos_erro(format('insert into public.animal_fotos (animal_id,posicao,estado) values (%L,3,''pronta'')',
    current_setting('adotae.foto_animal_b')), '42501');
select pg_temp.fotos_erro('insert into public.animal_fotos (animal_id,posicao) values (gen_random_uuid(),1)', '23503');
-- Simular uma configuração indevida e desfazê-la por SAVEPOINT: a auditoria
-- precisa detectar o grant amplo; o trigger continua barrando alterações mesmo
-- se houver também uma política UPDATE permissiva indevida. Não é uma correção
-- automática de grants: se a auditoria inicial falhar, parar para revisão.
savepoint privilegios_indevidos;
grant update on public.animal_fotos to authenticated;
create policy teste_fotos_update_amplo on public.animal_fotos
    for update to authenticated using (true) with check (true);
select pg_temp.fotos_assert(has_table_privilege('authenticated','public.animal_fotos','UPDATE'),
    'auditoria detecta UPDATE amplo');
select set_config('request.jwt.claim.sub', current_setting('adotae.teste_usuario_a'), true);
select set_config('request.jwt.claims', json_build_object(
    'sub', current_setting('adotae.teste_usuario_a'), 'role', 'authenticated')::text, true);
set local role authenticated;
select pg_temp.fotos_erro('update public.animal_fotos set estado = ''pronta'' where estado = ''pendente''', '42501');
select pg_temp.fotos_erro('update public.animal_fotos set estado = ''pendente'' where estado = ''removendo''', '42501');
select pg_temp.fotos_erro(format('update public.animal_fotos set %I = %s', c, valor), '42501')
from (values ('id','gen_random_uuid()'), ('animal_id','gen_random_uuid()'),
    ('posicao','4'), ('criado_em','now() + interval ''1 day''')) v(c,valor);
reset role;
rollback to savepoint privilegios_indevidos;
select pg_temp.fotos_assert(not has_table_privilege('authenticated','public.animal_fotos','UPDATE'),
    'grant indevido desfeito');
-- Estrutura das políticas Storage: nenhuma concessão de UPDATE escapa aos
-- guardas; não usar somente a existência dos nomes como prova de autorização.
select pg_temp.fotos_assert((select count(*) = 5 and bool_and(not polpermissive)
    from pg_policy where polrelid = 'storage.objects'::regclass
    and polname in ('animal_fotos_storage_papeis','animal_fotos_storage_insercao',
        'animal_fotos_storage_leitura','animal_fotos_storage_exclusao',
        'animal_fotos_storage_atualizacao')), 'cinco guardas RESTRICTIVE');
-- Grants de Storage pertencem ao serviço: não os modificar para fazer passar.
select pg_temp.fotos_assert(has_table_privilege('authenticated','storage.objects', p),
    'Storage exige privilégio existente: ' || p)
from unnest(array['SELECT','INSERT','DELETE']) p;
select pg_temp.fotos_assert((select a.acl is not distinct from c.relacl::text and a.politicas = (
    select jsonb_agg(to_jsonb(p) order by policyname) from pg_policies p
    where schemaname = 'public' and tablename = 'animais')
    from fotos_animais_antes a, pg_class c where c.oid = 'public.animais'::regclass),
    'políticas e ACLs de animais preservadas');
rollback;

-- CONFIRMAÇÃO COM OBJETO REAL: bloco separado, após preparação autorizada via
-- API Storage de uma reserva pendente própria em rascunho e seu arquivo WebP.
-- Nunca preparar por INSERT/UPDATE em storage.objects. Substituir os IDs, executar
-- instruções na mesma conexão e terminar com ROLLBACK (a confirmação será desfeita):
-- BEGIN;
-- SELECT set_config('request.jwt.claim.sub', '<UUID A>', true);
-- SELECT set_config('request.jwt.claims', '{"sub":"<UUID A>","role":"authenticated"}', true);
-- SET LOCAL ROLE authenticated;
-- SELECT id,animal_id,posicao,estado,criado_em,xmin,ctid FROM public.animal_fotos WHERE id='<foto>';
-- SELECT public.confirmar_foto('<foto>'); -- pronta; somente estado muda
-- SELECT id,animal_id,posicao,estado,criado_em,xmin,ctid FROM public.animal_fotos WHERE id='<foto>';
-- SELECT public.confirmar_foto('<foto>'); -- sucesso; xmin/ctid e demais campos preservados
-- SELECT id,animal_id,posicao,estado,criado_em,xmin,ctid FROM public.animal_fotos WHERE id='<foto>';
-- ROLLBACK;
-- Repetir como B/anon (42501), sem objeto (P0001), e em removendo (42501).
-- Caminho/bucket/owner_id divergentes: cobertos no Storage simulado local. No
-- serviço real, políticas devem impedir criar esses arquivos forjados; não
-- corromper metadados para forçar as fixtures. Validar por API A/B e outro bucket.
-- Ver test_animal_fotos_local.py para testes automatizados de sucesso, identidade,
-- metadados, divergências, idempotência e confirmação concorrente com remoção.

-- CONCORRÊNCIA (SQL, duas conexões; fora da transação acima): preparar um animal
-- rascunho A descartável e guardar o UUID. Nas duas conexões BEGIN, claims A e
-- SET LOCAL ROLE authenticated, como acima. Conexão 1 insere posição 1 e mantém
-- a transação aberta. Conexão 2 insere a mesma posição: deve aguardar. COMMIT na
-- 1 -> erro 23505 na 2; se ROLLBACK na 1 -> inserção 2 pode prosseguir. Repetir
-- para 2/3; posição 4 falha 23514. A contagem confirmada nunca excede 3. Encerrar
-- transações e limpar as fixtures por operador autorizado (não há DELETE cliente
-- de reserva). Não executar esses COMMITs no roteiro transacional acima.
--
-- API REAL DO STORAGE (pendente; roteiro detalhado em ../README.md): conferir
-- combinação efetiva das políticas, operações identificadas pelo servidor,
-- upload/download/delete, upsert, copy/move e URLs assinadas; SQL não grava bytes
-- e não prova essas operações. Nunca criar fixtures por DML em storage.objects.
