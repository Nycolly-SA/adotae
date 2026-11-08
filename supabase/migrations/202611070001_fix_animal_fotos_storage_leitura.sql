-- PROPOSTA: não aplicada remotamente. Requer 202611060001_create_animal_fotos.sql.
-- Compatibilidade com as duas operações de leitura autenticada documentadas:
-- https://supabase.com/docs/guides/storage/security/access-control
-- Hipótese para o 404 no download; não comprova a causa no serviço instalado.
-- ALTER POLICY preserva RESTRICTIVE, SELECT e o papel authenticated existentes.
-- Não altera bucket, grants, RPC, reservas ou as demais políticas.
begin;

alter policy animal_fotos_storage_leitura on storage.objects
    using (
        bucket_id <> 'animal-fotos' or (
            current_user = 'authenticated'
            and exists (
                select 1 from public.animal_fotos f
                join public.animais a on a.id = f.animal_id
                where a.usuario_id = (select auth.uid())
                  and name = a.usuario_id::text || '/' || a.id::text || '/' || f.id::text || '.webp'
                  and (
                      (f.estado = 'pronta' and storage.allow_any_operation(
                          array['object.get_authenticated', 'object.get_authenticated_info']))
                      -- Preserva o SELECT auxiliar exigido pela API de remoção.
                      or (f.estado = 'removendo' and storage.allow_any_operation(
                          array['object.delete', 'object.delete_many']))
                  )
            )
        )
    );

commit;
