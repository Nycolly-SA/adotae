-- PROPOSTA: não aplicada remotamente. Execução única após as migrações anteriores.
begin;

revoke update (status_adocao) on public.animais from authenticated;

-- REVOKE por coluna não remove grants de tabela, PUBLIC ou papéis herdados.
-- Se houver divergência, abortar a transação para revisão, sem ampliar direitos
-- nem revogar permissões de outros papéis silenciosamente.
do $$
declare coluna text;
begin
    foreach coluna in array array[
        'id', 'usuario_id', 'status_adocao', 'status_publicacao',
        'oculto_admin', 'criado_em', 'atualizado_em'
    ] loop
        if has_column_privilege('authenticated', 'public.animais', coluna, 'UPDATE') then
            raise exception 'authenticated mantém UPDATE efetivo em %; revisar grants', coluna;
        end if;
    end loop;
end;
$$;

create function public.animais_validar_edicao()
returns trigger
language plpgsql
security invoker
set search_path = ''
as $$
begin
    -- Mesmo critério de papéis confiáveis usado nas fotografias. Não confiar
    -- em claims JWT; esta exceção não concede grants nem contorna RLS.
    if current_user in ('postgres', 'service_role') then
        return new;
    end if;

    if row(new.id, new.usuario_id, new.status_publicacao, new.status_adocao,
           new.oculto_admin, new.criado_em)
       is distinct from
       row(old.id, old.usuario_id, old.status_publicacao, old.status_adocao,
           old.oculto_admin, old.criado_em) then
        raise exception 'Campos protegidos do anúncio não podem ser alterados'
            using errcode = '42501';
    end if;

    -- Comparação NULL-safe de toda a linha, exceto contatos e data automática.
    -- Novas colunas também ficam protegidas em publicados por padrão.
    -- BEFORE triggers de mesmo tipo executam por nome: animais_atualizar_data
    -- já alterou atualizado_em. O cliente continua sem UPDATE nessa coluna.
    if old.status_publicacao = 'publicado'
       and (to_jsonb(new) - array['email_contato', 'whatsapp', 'atualizado_em'])
           is distinct from
           (to_jsonb(old) - array['email_contato', 'whatsapp', 'atualizado_em']) then
        raise exception 'Anúncio publicado permite alterar somente os contatos'
            using errcode = '42501';
    end if;
    -- CHECKs existentes validam formato e exigem contato em publicados.
    return new;
end;
$$;

revoke all on function public.animais_validar_edicao()
    from public, anon, authenticated;

create trigger animais_validar_edicao
    before update on public.animais
    for each row execute function public.animais_validar_edicao();

commit;
