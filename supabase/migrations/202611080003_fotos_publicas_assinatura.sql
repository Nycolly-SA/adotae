-- PROPOSTA: não aplicada remotamente. Requer todas as migrações anteriores.
begin;

-- Metadados mínimos usados pelo predicado Storage e para localizar reservas.
-- Não conceder SELECT de tabela: posicao/criado_em não são necessários aqui.
grant select (id, animal_id, estado) on public.animal_fotos to anon;

create policy animal_fotos_leitura_publica on public.animal_fotos
    for select to anon using (
        estado = 'pronta' and exists (
            select 1 from public.animais a where a.id = animal_id
              and a.status_publicacao = 'publicado'
              and a.status_adocao = 'disponivel' and a.oculto_admin = false
        )
    );
-- A mesma fronteira deve sobreviver a outras políticas PERMISSIVE.
create policy animal_fotos_limite_publico on public.animal_fotos
    as restrictive for select to anon using (
        estado = 'pronta' and exists (
            select 1 from public.animais a where a.id = animal_id
              and a.status_publicacao = 'publicado'
              and a.status_adocao = 'disponivel' and a.oculto_admin = false
        )
    );

-- USING admite o visitante; WITH CHECK continua exclusivo de authenticated.
-- As guardas novas abaixo impedem DELETE/UPDATE inclusive com outra base ampla.
alter policy animal_fotos_storage_papeis on storage.objects
    using (bucket_id <> 'animal-fotos' or current_user in ('authenticated', 'anon'));

create policy animal_fotos_storage_assinatura_publica on storage.objects
    for select to anon using (bucket_id = 'animal-fotos');

create policy animal_fotos_storage_limite_anon on storage.objects
    as restrictive for select to anon using (
        case when bucket_id <> 'animal-fotos' then true else
            current_user = 'anon'
            and storage.allow_only_operation('object.sign')
            and exists (
                select 1 from public.animal_fotos f
                join public.animais a on a.id = f.animal_id
                where f.estado = 'pronta'
                  and a.status_publicacao = 'publicado'
                  and a.status_adocao = 'disponivel' and a.oculto_admin = false
                  and owner_id = a.usuario_id::text
                  and name = a.usuario_id::text || '/' || a.id::text || '/' || f.id::text || '.webp'
            )
        end
    );

create policy animal_fotos_storage_anon_sem_update on storage.objects
    as restrictive for update to anon
    using (bucket_id <> 'animal-fotos')
    with check (bucket_id <> 'animal-fotos');
create policy animal_fotos_storage_anon_sem_delete on storage.objects
    as restrictive for delete to anon using (bucket_id <> 'animal-fotos');

-- Não alterar grants do Storage, bucket, políticas authenticated ou outros buckets.
-- Detectar grants herdados/defaults incompatíveis em vez de escondê-los.
do $$
declare coluna record;
begin
    if not (select relrowsecurity from pg_class where oid='storage.objects'::regclass)
       or not (select relrowsecurity from pg_class where oid='public.animal_fotos'::regclass)
       or not (select relrowsecurity from pg_class where oid='public.animais'::regclass) then
        raise exception 'RLS obrigatória em objetos, reservas e anúncios';
    end if;
    for coluna in select attname from pg_attribute
        where attrelid='public.animal_fotos'::regclass and attnum>0 and not attisdropped
    loop
        if has_column_privilege('anon','public.animal_fotos',coluna.attname,'SELECT')
           is distinct from (coluna.attname in ('id','animal_id','estado'))
           or has_column_privilege('anon','public.animal_fotos',coluna.attname,'INSERT')
           or has_column_privilege('anon','public.animal_fotos',coluna.attname,'UPDATE') then
            raise exception 'anon mantém privilégios incompatíveis em animal_fotos.%', coluna.attname;
        end if;
    end loop;
end;
$$;

commit;
