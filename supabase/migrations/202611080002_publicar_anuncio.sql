-- PROPOSTA: não aplicada remotamente. Requer todas as migrações anteriores.
begin;

-- Não ampliar grants para permitir publicação; abortar em caso de divergência.
do $$
begin
    if has_column_privilege('authenticated', 'public.animais', 'status_publicacao', 'UPDATE') then
        raise exception 'authenticated mantém UPDATE efetivo em status_publicacao; revisar grants';
    end if;
end;
$$;

create function adotae_private.publicar_anuncio(p_animal_id uuid)
returns void
language plpgsql
security definer
set search_path = ''
as $$
declare
    v_usuario uuid := auth.uid();
    v_animal public.animais%rowtype;
    v_objeto uuid;
    v_fotos uuid[];
begin
    if v_usuario is null or p_animal_id is null then
        raise exception 'Publicação não autorizada' using errcode = '42501';
    end if;
    -- Verificar Auth real, nunca email/claims fornecidos pelo cliente.
    perform 1 from auth.users u
        where u.id = v_usuario and u.email_confirmed_at is not null for share nowait;
    if not found then
        raise exception 'Publicação não autorizada' using errcode = '42501';
    end if;

    -- Todas as operações seguem foto -> animal. Ordenação evita inversões entre
    -- publicações do mesmo anúncio. NOWAIT evita ciclos com UPDATEs de remoção
    -- em lote que podem visitar fotos em outra ordem; 55P03 permite nova tentativa.
    -- Filtrar propriedade antes de bloquear evita bloquear fotografias alheias.
    select coalesce(array_agg(bloqueadas.id), '{}'::uuid[]) into v_fotos
        from (
            select f.id from public.animal_fotos f
            where f.animal_id = p_animal_id and exists (
                select 1 from public.animais a where a.id = f.animal_id and a.usuario_id = v_usuario
            ) order by f.id for update of f nowait
        ) bloqueadas;

    select a.* into v_animal from public.animais a
        where a.id = p_animal_id and a.usuario_id = v_usuario for no key update nowait;
    if not found then
        raise exception 'Publicação não autorizada' using errcode = '42501';
    end if;
    if v_animal.status_publicacao <> 'rascunho'
       or v_animal.status_adocao <> 'disponivel' or v_animal.oculto_admin
       or (v_animal.whatsapp is null and v_animal.email_contato is null) then
        raise exception 'Publicação não autorizada' using errcode = '42501';
    end if;
    -- CHECKs existentes garantem o formato dos contatos. Ler novamente após
    -- adquirir os locks: confirmação/remoção pode ter terminado durante a espera.
    -- Bloquear também o objeto impede DELETE concorrente até o COMMIT.
    select o.id into v_objeto from public.animal_fotos f
        join storage.objects o on o.bucket_id = 'animal-fotos'
            and o.owner_id = v_usuario::text
            and o.name = v_usuario::text || '/' || p_animal_id::text || '/' || f.id::text || '.webp'
        where f.animal_id = p_animal_id and f.id = any(v_fotos) and f.estado = 'pronta'
        order by f.id limit 1 for share of o nowait;
    if not found then
        raise exception 'Publicação não autorizada' using errcode = '42501';
    end if;

    -- Reservas inseridas depois da primeira consulta não foram bloqueadas e
    -- nunca podem ser a prova de publicação desta chamada. Basta tentar de novo.

    -- O trigger existente continua ativo; postgres é seu executor confiável.
    update public.animais set status_publicacao = 'publicado' where id = p_animal_id;
end;
$$;
alter function adotae_private.publicar_anuncio(uuid) owner to postgres;
revoke all on function adotae_private.publicar_anuncio(uuid) from public, anon, authenticated, service_role;
grant execute on function adotae_private.publicar_anuncio(uuid) to authenticated;

create function public.publicar_anuncio(p_animal_id uuid)
returns void
language sql
security invoker
set search_path = ''
as $$
    select adotae_private.publicar_anuncio(p_animal_id);
$$;
alter function public.publicar_anuncio(uuid) owner to postgres;
revoke all on function public.publicar_anuncio(uuid) from public, anon, authenticated, service_role;
grant execute on function public.publicar_anuncio(uuid) to authenticated;

-- UPDATE já bloqueou a foto antes do BEFORE trigger. SHARE do animal conflita
-- com publicação e revalida seu estado após espera. Não confiar só em RLS com
-- snapshot anterior à espera; não alterar políticas/grants do Storage.
create function public.animal_fotos_proteger_publicadas()
returns trigger
language plpgsql
security invoker
set search_path = ''
as $$
declare
    v_status text;
begin
    if current_user in ('postgres', 'service_role') then
        return new;
    end if;
    select a.status_publicacao into v_status from public.animais a
        where a.id = old.animal_id for share;
    if not found or v_status <> 'rascunho' then
        raise exception 'Fotografias de anúncio publicado não podem ser removidas'
            using errcode = '42501';
    end if;
    return new;
end;
$$;
alter function public.animal_fotos_proteger_publicadas() owner to postgres;
revoke all on function public.animal_fotos_proteger_publicadas() from public, anon, authenticated;
create trigger animal_fotos_proteger_publicadas
    before update on public.animal_fotos
    for each row execute function public.animal_fotos_proteger_publicadas();

-- A confirmação é redefinida abaixo apenas para tornar explícita a mesma ordem
-- de locks (foto -> animal), com revalidação de propriedade após a espera.

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
    select a.status_publicacao into v_status from public.animais a
        where a.id = v_reserva.animal_id and a.usuario_id = v_usuario for share;
    if not found then
        raise exception 'Confirmação de fotografia não autorizada' using errcode = '42501';
    end if;
    if v_status <> 'rascunho'
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
