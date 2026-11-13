-- PROPOSTA: somente validação local; requer todas as migrações anteriores.
begin;

alter table public.animais add column removido_em timestamptz;
revoke delete on public.animais from authenticated;
drop policy animais_exclusao_propria on public.animais;

-- REVOKE direto não elimina PUBLIC nem herança. Abortar em caso de drift.
do $$
begin
    if has_table_privilege('authenticated', 'public.animais', 'DELETE') then
        raise exception 'authenticated mantém DELETE efetivo em animais; revisar grants';
    end if;
    if has_column_privilege('authenticated', 'public.animais', 'removido_em', 'UPDATE')
       or has_column_privilege('authenticated', 'public.animais', 'removido_em', 'INSERT') then
        raise exception 'authenticated mantém escrita efetiva em removido_em; revisar grants';
    end if;
    if 'adotae_private' = any(regexp_split_to_array(
        coalesce(current_setting('pgrst.db_schemas', true), ''), '\s*,\s*')) then
        raise exception 'adotae_private deve ficar fora dos Exposed schemas da Data API';
    end if;
end;
$$;

create function adotae_private.remover_anuncio(p_animal_id uuid)
returns void
language plpgsql
security definer
set search_path = ''
as $$
declare
    v_usuario uuid := auth.uid();
    v_animal public.animais%rowtype;
begin
    -- Nunca bloquear fotos/objetos depois do animal. NO KEY UPDATE é compatível
    -- com as FKs das reservas, mas conflita com SHARE das operações de fotos.
    select a.* into v_animal from public.animais a
        where a.id = p_animal_id and a.usuario_id = v_usuario for no key update;
    if v_usuario is null or not found then
        raise exception 'Remoção não autorizada' using errcode = '42501';
    end if;
    -- Revalidar a versão corrente depois da espera, inclusive estados inesperados.
    if v_animal.status_adocao <> 'disponivel'
       or v_animal.status_publicacao not in ('rascunho', 'publicado') then
        raise exception 'Remoção não autorizada' using errcode = '42501';
    end if;
    if v_animal.removido_em is not null then
        return;
    end if;
    -- Não alterar status, ocultação, fotos, reservas ou Storage.
    update public.animais set removido_em = clock_timestamp() where id = p_animal_id;
end;
$$;
alter function adotae_private.remover_anuncio(uuid) owner to postgres;
revoke all on function adotae_private.remover_anuncio(uuid) from public, anon, authenticated, service_role;
grant execute on function adotae_private.remover_anuncio(uuid) to authenticated;

create function public.remover_anuncio(p_animal_id uuid)
returns void
language sql
security invoker
set search_path = ''
as $$
    select adotae_private.remover_anuncio(p_animal_id);
$$;
alter function public.remover_anuncio(uuid) owner to postgres;
revoke all on function public.remover_anuncio(uuid) from public, anon, authenticated, service_role;
grant execute on function public.remover_anuncio(uuid) to authenticated;

alter policy animais_leitura_publica on public.animais using (
    removido_em is null and status_publicacao = 'publicado'
    and status_adocao = 'disponivel' and not oculto_admin
);
-- Mantém a leitura técnica própria. Uma base permissiva adicional não pode
-- reabrir anúncios removidos para visitantes ou outros usuários da Data API.
create policy animais_limite_removidos on public.animais
    as restrictive for select to anon, authenticated using (
        removido_em is null or
        (current_user = 'authenticated' and usuario_id = (select auth.uid()))
    );
create policy animais_sem_edicao_removidos on public.animais
    as restrictive for update to authenticated
    using (removido_em is null) with check (removido_em is null);
create policy animais_sem_insercao_removidos on public.animais
    as restrictive for insert to authenticated with check (removido_em is null);

-- Além dos grants, proteger a coluna contra concessões acidentais e bloquear
-- UPDATEs das RPCs DEFINER existentes após esperarem pelo lock da remoção.
create function public.animais_proteger_remocao()
returns trigger
language plpgsql
security invoker
set search_path = ''
as $$
begin
    if tg_op = 'UPDATE' then
        if old.removido_em is not null then
            raise exception 'Anúncio removido não pode ser alterado' using errcode = '42501';
        end if;
        if new.removido_em is distinct from old.removido_em
           and current_user <> 'postgres' then
            raise exception 'Remoção exige RPC autorizada' using errcode = '42501';
        end if;
    elsif new.removido_em is not null then
        raise exception 'Anúncio deve iniciar sem remoção' using errcode = '42501';
    end if;
    return new;
end;
$$;
revoke all on function public.animais_proteger_remocao() from public, anon, authenticated, service_role;
create trigger animais_proteger_remocao before insert or update on public.animais
    for each row execute function public.animais_proteger_remocao();

create policy animal_fotos_limite_removidos on public.animal_fotos
    as restrictive for select to anon, authenticated using (
        exists (select 1 from public.animais a where a.id = animal_id
            and (a.removido_em is null or
                (current_user = 'authenticated' and a.usuario_id = (select auth.uid()))))
    );
create policy animal_fotos_sem_reserva_removidos on public.animal_fotos
    as restrictive for insert to authenticated with check (
        exists (select 1 from public.animais a where a.id = animal_id and a.removido_em is null)
    );
create policy animal_fotos_sem_alteracao_removidos on public.animal_fotos
    as restrictive for update to authenticated
    using (exists (select 1 from public.animais a where a.id = animal_id and a.removido_em is null))
    with check (exists (select 1 from public.animais a where a.id = animal_id and a.removido_em is null));

create function public.animal_fotos_proteger_remocao()
returns trigger
language plpgsql
security invoker
set search_path = ''
as $$
declare
    v_removido timestamptz;
begin
    -- UPDATE já bloqueou a foto; INSERT ainda não adquiriu o lock da FK.
    -- SHARE, e não KEY SHARE, serializa também criação de novas reservas.
    select a.removido_em into v_removido from public.animais a
        where a.id = new.animal_id for share;
    -- FOR SHARE também aplica a política de UPDATE: depois de esperar pela
    -- remoção ela pode filtrar a linha. Não confundir isso com FK inexistente.
    if not found then
        if exists (select 1 from public.animais a where a.id = new.animal_id) then
            raise exception 'Fotografias de anúncio removido não podem ser alteradas' using errcode = '42501';
        end if;
        -- Animal realmente ausente continua sujeito à RLS/FK originais.
    elsif v_removido is not null then
        raise exception 'Fotografias de anúncio removido não podem ser alteradas' using errcode = '42501';
    end if;
    return new;
end;
$$;
revoke all on function public.animal_fotos_proteger_remocao() from public, anon, authenticated, service_role;
create trigger animal_fotos_proteger_remocao before insert or update on public.animal_fotos
    for each row execute function public.animal_fotos_proteger_remocao();

-- Guarda neutra nos outros buckets. Vale para download, assinatura, upload,
-- DELETE auxiliar e ambos os lados de UPDATE, sem alterar grants do Storage.
create policy animal_fotos_storage_sem_removidos on storage.objects
    as restrictive for all to anon, authenticated
    using (
        bucket_id <> 'animal-fotos' or exists (
            select 1 from public.animal_fotos f join public.animais a on a.id = f.animal_id
            where a.removido_em is null
              and name = a.usuario_id::text || '/' || a.id::text || '/' || f.id::text || '.webp'
        )
    )
    with check (
        bucket_id <> 'animal-fotos' or exists (
            select 1 from public.animal_fotos f join public.animais a on a.id = f.animal_id
            where a.removido_em is null
              and name = a.usuario_id::text || '/' || a.id::text || '/' || f.id::text || '.webp'
        )
    );

-- A confirmação repetida não executa UPDATE: precisa revalidar explicitamente
-- a remoção após adquirir SHARE, mesmo quando a reserva já está pronta.
create or replace function adotae_private.confirmar_foto(p_foto_id uuid)
returns void
language plpgsql
security definer
set search_path = ''
as $$
declare
    v_usuario uuid := auth.uid();
    v_reserva record;
    v_status text;
    v_removido timestamptz;
    v_metadata jsonb;
    v_tamanho text;
    v_mime text;
begin
    if v_usuario is null or p_foto_id is null then
        raise exception 'Confirmação de fotografia não autorizada' using errcode = '42501';
    end if;

    -- Bloquear explicitamente foto primeiro, animal depois (como publicação e
    -- remoção); não depender da ordem de execução de locks de um JOIN.
    select f.id, f.animal_id, f.estado into v_reserva
        from public.animal_fotos f where f.id = p_foto_id and exists (
            select 1 from public.animais a where a.id = f.animal_id and a.usuario_id = v_usuario
        ) for update of f;
    if not found then
        raise exception 'Confirmação de fotografia não autorizada' using errcode = '42501';
    end if;
    select a.status_publicacao, a.removido_em into v_status, v_removido from public.animais a
        where a.id = v_reserva.animal_id and a.usuario_id = v_usuario for share;
    if not found then
        raise exception 'Confirmação de fotografia não autorizada' using errcode = '42501';
    end if;
    if v_removido is not null or v_status <> 'rascunho'
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


commit;
