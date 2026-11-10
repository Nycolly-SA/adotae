-- PROPOSTA: não aplicada remotamente. Requer todas as migrações anteriores.
begin;

-- Não ampliar grants nem ocultar divergências nas proteções de UPDATE direto.
do $$
begin
    if has_column_privilege('authenticated', 'public.animais', 'status_adocao', 'UPDATE') then
        raise exception 'authenticated mantém UPDATE efetivo em status_adocao; revisar grants';
    end if;
end;
$$;

create function adotae_private.marcar_adotado(p_animal_id uuid)
returns void
language plpgsql
security definer
set search_path = ''
as $$
declare
    v_usuario uuid := auth.uid();
    v_animal public.animais%rowtype;
begin
    if v_usuario is null or p_animal_id is null then
        raise exception 'Adoção não autorizada' using errcode = '42501';
    end if;

    -- Bloquear somente o anúncio próprio, sem adquirir locks de fotos/objetos
    -- depois dele. NO KEY UPDATE não conflita com KEY SHARE das FKs das fotos.
    -- Aguardar outra adoção e revalidar a versão corrente após a espera.
    select a.* into v_animal from public.animais a
        where a.id = p_animal_id and a.usuario_id = v_usuario for no key update;
    if not found then
        raise exception 'Adoção não autorizada' using errcode = '42501';
    end if;
    if v_animal.status_publicacao <> 'publicado' or v_animal.status_adocao <> 'disponivel' then
        raise exception 'Adoção não autorizada' using errcode = '42501';
    end if;

    -- Ocultação administrativa não impede adoção nem é alterada. Triggers e
    -- RLS permanecem intactos; o trigger existente atualiza atualizado_em.
    update public.animais set status_adocao = 'adotado' where id = p_animal_id;
end;
$$;
alter function adotae_private.marcar_adotado(uuid) owner to postgres;
revoke all on function adotae_private.marcar_adotado(uuid) from public, anon, authenticated, service_role;
grant execute on function adotae_private.marcar_adotado(uuid) to authenticated;

create function public.marcar_adotado(p_animal_id uuid)
returns void
language sql
security invoker
set search_path = ''
as $$
    select adotae_private.marcar_adotado(p_animal_id);
$$;
alter function public.marcar_adotado(uuid) owner to postgres;
revoke all on function public.marcar_adotado(uuid) from public, anon, authenticated, service_role;
grant execute on function public.marcar_adotado(uuid) to authenticated;

commit;
