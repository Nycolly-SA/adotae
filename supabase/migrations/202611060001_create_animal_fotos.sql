-- PROPOSTA: não aplicada remotamente. Execução única, após revisão.
-- Requer a migração de animais, Supabase Auth/Storage e os helpers de operação
-- documentados em https://supabase.com/docs/guides/storage/schema/helper-functions
-- Não cria bucket, não grava metadados de arquivos e não altera public.animais.
begin;

-- Falhar antes de qualquer mudança se o Storage instalado for incompatível.
do $$
begin
    if to_regprocedure('storage.allow_only_operation(text)') is null
       or to_regprocedure('storage.allow_any_operation(text[])') is null then
        raise exception 'Storage sem helpers de operação; revisar versão antes de aplicar';
    end if;
    if not (select relrowsecurity from pg_class where oid = 'storage.objects'::regclass) then
        raise exception 'storage.objects precisa de RLS habilitada';
    end if;
    if 'adotae_private' = any(regexp_split_to_array(
        coalesce(current_setting('pgrst.db_schemas', true), ''), '\s*,\s*')) then
        raise exception 'adotae_private deve ficar fora dos Exposed schemas da Data API';
    end if;
end;
$$;

create table public.animal_fotos (
    id uuid primary key default gen_random_uuid(),
    animal_id uuid not null references public.animais(id) on delete no action,
    posicao smallint not null check (posicao between 1 and 3),
    estado text not null default 'pendente'
        check (estado in ('pendente', 'pronta', 'removendo')),
    criado_em timestamptz not null default now(),
    constraint animal_fotos_animal_posicao_key unique (animal_id, posicao)
);
-- O índice UNIQUE já atende a FK e consultas por animal. Não usar COUNT para
-- limitar reservas: CHECK + UNIQUE mantêm no máximo três mesmo sob concorrência.

create function public.animal_fotos_validar_alteracao()
returns trigger
language plpgsql
security invoker
set search_path = ''
as $$
begin
    if tg_op = 'INSERT' then
        if new.estado is distinct from 'pendente' then
            raise exception 'A reserva deve iniciar pendente' using errcode = '42501';
        end if;
    else
        if new.id is distinct from old.id
           or new.animal_id is distinct from old.animal_id
           or new.posicao is distinct from old.posicao
           or new.criado_em is distinct from old.criado_em then
            raise exception 'Identificação, vínculo, posição e criação são imutáveis'
                using errcode = '42501';
        end if;
        -- UPDATE direto do cliente só solicita remoção. A auxiliar privada de
        -- confirmação executa como postgres, sem afirmar que validou os bytes.
        -- service_role continua reservado, nunca ao navegador ou ao Flask.
        if not (
            (old.estado in ('pendente', 'pronta') and new.estado = 'removendo')
            or (old.estado = 'pendente' and new.estado = 'pronta'
                and current_user in ('postgres', 'service_role'))
        ) or new.estado is null then
            raise exception 'Transição de fotografia não permitida' using errcode = '42501';
        end if;
    end if;
    return new;
end;
$$;
revoke all on function public.animal_fotos_validar_alteracao()
    from public, anon, authenticated;
create trigger animal_fotos_validar_alteracao
    before insert or update on public.animal_fotos
    for each row execute function public.animal_fotos_validar_alteracao();

alter table public.animal_fotos enable row level security;
-- Limpar os grants padrão, inclusive os eventualmente amplos do schema public.
revoke all on table public.animal_fotos from public, anon, authenticated;
grant select on table public.animal_fotos to authenticated;
grant insert (animal_id, posicao) on public.animal_fotos to authenticated;
grant update (estado) on public.animal_fotos to authenticated;

create policy animal_fotos_leitura_propria on public.animal_fotos
    for select to authenticated using (
        exists (select 1 from public.animais a
            where a.id = animal_id and a.usuario_id = (select auth.uid()))
    );
create policy animal_fotos_reserva_propria on public.animal_fotos
    for insert to authenticated with check (
        estado = 'pendente'
        and exists (select 1 from public.animais a
            where a.id = animal_id and a.usuario_id = (select auth.uid())
              and a.status_publicacao = 'rascunho')
    );
create policy animal_fotos_solicitar_remocao on public.animal_fotos
    for update to authenticated
    using (
        estado in ('pendente', 'pronta')
        and exists (select 1 from public.animais a
            where a.id = animal_id and a.usuario_id = (select auth.uid()))
    )
    with check (
        estado = 'removendo'
        and exists (select 1 from public.animais a
            where a.id = animal_id and a.usuario_id = (select auth.uid()))
    );
-- Sem DELETE de reservas: removendo é terminal para o cliente e ocupa a posição.
-- Um futuro processo confiável verificará a remoção real antes da limpeza da
-- reserva. Não consultar/excluir storage.objects diretamente para essa limpeza.

-- A entrada pública não tem privilégios elevados. USAGE/EXECUTE da auxiliar são
-- necessários para a delegação INVOKER; acesso SQL direto à auxiliar também faz
-- TODAS as verificações. Este schema não pode integrar os Exposed schemas.
-- CREATE SCHEMA sem IF NOT EXISTS: divergências devem interromper a migração.
create schema adotae_private authorization postgres;
revoke all on schema adotae_private from public, anon, authenticated;
grant usage on schema adotae_private to authenticated;

create function adotae_private.confirmar_foto(p_foto_id uuid)
returns void
language plpgsql
security definer
set search_path = ''
as $$
declare
    v_usuario uuid := auth.uid();
    v_reserva record;
    v_metadata jsonb;
    v_tamanho text;
    v_mime text;
begin
    if v_usuario is null or p_foto_id is null then
        raise exception 'Confirmação de fotografia não autorizada' using errcode = '42501';
    end if;

    -- Filtrar propriedade ANTES de bloquear evita bloquear reserva alheia.
    -- O lock da foto serializa confirmação/remoção; o do animal preserva o
    -- rascunho até o fim da operação. Nenhuma coluna de animais é modificada.
    select f.id, f.animal_id, f.estado, a.usuario_id, a.status_publicacao
        into v_reserva
        from public.animal_fotos f join public.animais a on a.id = f.animal_id
        where f.id = p_foto_id and a.usuario_id = v_usuario
        for update of f for share of a;
    if not found then
        raise exception 'Confirmação de fotografia não autorizada' using errcode = '42501';
    end if;
    if v_reserva.status_publicacao <> 'rascunho'
       or v_reserva.estado not in ('pendente', 'pronta') then
        raise exception 'Confirmação de fotografia não autorizada' using errcode = '42501';
    end if;

    -- Somente leitura do catálogo. Não conceder SELECT adicional no Storage ao
    -- chamador nem falsificar storage.operation para contornar suas políticas.
    select o.metadata into v_metadata from storage.objects o
        where o.bucket_id = 'animal-fotos'
          and o.name = v_usuario::text || '/' || v_reserva.animal_id::text
                       || '/' || v_reserva.id::text || '.webp'
          and o.owner_id = v_usuario::text;
    if not found then
        raise exception 'Objeto correspondente à reserva não encontrado' using errcode = 'P0001';
    end if;

    -- Campos ausentes/JSON null são indisponíveis; valores presentes precisam
    -- ser compatíveis com o bucket. São metadados, não prova dos bytes WebP.
    v_tamanho := v_metadata ->> 'size';
    v_mime := v_metadata ->> 'mimetype';
    if v_tamanho is not null then
        if v_tamanho !~ '^[0-9]{1,6}$' then
            raise exception 'Tamanho de fotografia incompatível' using errcode = '22023';
        end if;
        if v_tamanho::integer not between 1 and 512000 then
            raise exception 'Tamanho de fotografia incompatível' using errcode = '22023';
        end if;
    end if;
    if v_mime is not null and v_mime <> 'image/webp' then
        raise exception 'MIME de fotografia incompatível' using errcode = '22023';
    end if;

    -- Repetição valida de novo propriedade/rascunho/objeto, sem disparar trigger
    -- ou criar versão de linha nova. Não reverte removendo e não publica animal.
    if v_reserva.estado = 'pendente' then
        update public.animal_fotos set estado = 'pronta' where id = v_reserva.id;
    end if;
end;
$$;
alter function adotae_private.confirmar_foto(uuid) owner to postgres;
revoke all on function adotae_private.confirmar_foto(uuid) from public, anon, authenticated, service_role;
grant execute on function adotae_private.confirmar_foto(uuid) to authenticated;

create function public.confirmar_foto(p_foto_id uuid)
returns void
language sql
security invoker
set search_path = ''
as $$
    select adotae_private.confirmar_foto(p_foto_id);
$$;
alter function public.confirmar_foto(uuid) owner to postgres;
revoke all on function public.confirmar_foto(uuid) from public, anon, authenticated, service_role;
grant execute on function public.confirmar_foto(uuid) to authenticated;

-- STORAGE: não alterar GRANTs globais do serviço nem políticas de outros buckets.
-- A política permissiva fornece a base; as RESTRICTIVE delimitam cada
-- comando, inclusive se outra política permissiva já autorizar todos os buckets.
-- Para outros buckets, os guardas são neutros (true), sem conceder acesso novo.
create policy animal_fotos_storage_base on storage.objects
    for all to authenticated
    using (bucket_id = 'animal-fotos')
    with check (bucket_id = 'animal-fotos');

-- Sem subconsultas para anon: esse papel não pode consultar animal_fotos,
-- inclusive durante a avaliação de políticas em buckets que não são este.
create policy animal_fotos_storage_papeis on storage.objects
    as restrictive for all to public
    using (bucket_id <> 'animal-fotos' or current_user = 'authenticated')
    with check (bucket_id <> 'animal-fotos' or current_user = 'authenticated');

create policy animal_fotos_storage_insercao on storage.objects
    as restrictive for insert to authenticated with check (
        bucket_id <> 'animal-fotos' or (
            current_user = 'authenticated'
            and storage.allow_only_operation('object.upload')
            and exists (
                select 1 from public.animal_fotos f
                join public.animais a on a.id = f.animal_id
                where a.usuario_id = (select auth.uid())
                  and a.status_publicacao = 'rascunho' and f.estado = 'pendente'
                  and name = a.usuario_id::text || '/' || a.id::text || '/' || f.id::text || '.webp'
            )
        )
    );

create policy animal_fotos_storage_leitura on storage.objects
    as restrictive for select to authenticated using (
        bucket_id <> 'animal-fotos' or (
            current_user = 'authenticated'
            and exists (
                select 1 from public.animal_fotos f
                join public.animais a on a.id = f.animal_id
                where a.usuario_id = (select auth.uid())
                  and name = a.usuario_id::text || '/' || a.id::text || '/' || f.id::text || '.webp'
                  and (
                      (f.estado = 'pronta' and storage.allow_only_operation('object.get_authenticated'))
                      -- A API de remoção também exige SELECT; conceder somente
                      -- durante DELETE, sem liberar download/assinatura/listagem.
                      or (f.estado = 'removendo' and storage.allow_any_operation(
                          array['object.delete', 'object.delete_many']))
                  )
            )
        )
    );

create policy animal_fotos_storage_exclusao on storage.objects
    as restrictive for delete to authenticated using (
        bucket_id <> 'animal-fotos' or (
            current_user = 'authenticated'
            and storage.allow_any_operation(array['object.delete', 'object.delete_many'])
            and exists (
                select 1 from public.animal_fotos f
                join public.animais a on a.id = f.animal_id
                where a.usuario_id = (select auth.uid()) and f.estado = 'removendo'
                  and name = a.usuario_id::text || '/' || a.id::text || '/' || f.id::text || '.webp'
            )
        )
    );

create policy animal_fotos_storage_atualizacao on storage.objects
    as restrictive for update to authenticated
    using (bucket_id <> 'animal-fotos')
    with check (bucket_id <> 'animal-fotos');
-- Bloqueia origem E destino de UPDATE; copy também é excluído pelos filtros de
-- operação de SELECT/INSERT. Sem upsert, signed upload, S3 ou TUS nesta etapa.
-- Os helpers retornam false sem contexto de operação: SQL direto não é a API.

commit;
