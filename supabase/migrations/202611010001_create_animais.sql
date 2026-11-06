-- Aplicada em adotae-testes e no projeto principal (ver ../README.md).
-- Migração inicial, transacional e de execução única; requer Supabase Auth.
begin;

create table public.animais (
    id uuid primary key default gen_random_uuid(),
    usuario_id uuid not null default auth.uid() references auth.users(id),
    nome text not null check (nome ~ '[^[:space:]]' and char_length(nome) <= 100),
    especie text not null check (especie in (
        'Cachorro', 'Gato', 'Ave', 'Pequeno mamífero', 'Outro animal doméstico'
    )),
    especie_outro text check (
        especie_outro ~ '[^[:space:]]' and char_length(especie_outro) <= 60
    ),
    sexo text not null check (sexo in ('Macho', 'Fêmea')),
    idade_aproximada text not null check (
        idade_aproximada ~ '[^[:space:]]' and char_length(idade_aproximada) <= 50
    ),
    porte text not null check (porte in ('Pequeno', 'Médio', 'Grande')),
    descricao text not null check (
        descricao ~ '[^[:space:]]' and char_length(descricao) <= 2000
    ),
    distrito text not null check (distrito in (
        'São João da Barra (sede)', 'Atafona', 'Grussaí',
        'Cajueiro', 'Pipeiras', 'Barcelos'
    )),
    bairro text check (bairro ~ '[^[:space:]]' and char_length(bairro) <= 100),
    -- Opcionais no rascunho; ausentes devem ser NULL, nunca string vazia.
    whatsapp text check (whatsapp ~ '^[1-9][0-9]{7,14}$'),
    email_contato text check (
        email_contato ~ '^[^[:space:]@]+@[^[:space:]@]+[.][^[:space:]@]+$'
    ),
    status_publicacao text not null default 'rascunho'
        check (status_publicacao in ('rascunho', 'publicado')),
    status_adocao text not null default 'disponivel'
        check (status_adocao in ('disponivel', 'adotado')),
    oculto_admin boolean not null default false,
    criado_em timestamptz not null default now(),
    atualizado_em timestamptz not null default now(),
    constraint animais_especie_outro_consistente check (
        (especie = 'Outro animal doméstico' and especie_outro is not null)
        or (especie <> 'Outro animal doméstico' and especie_outro is null)
    ),
    constraint animais_contato_publicacao check (
        status_publicacao <> 'publicado'
        or whatsapp is not null or email_contato is not null
    )
);

-- A FK mantém NO ACTION: não decide exclusão automática de anúncios/conta.
create index animais_usuario_id_idx on public.animais (usuario_id);
-- Índices separados permitem filtros independentes e suas combinações.
create index animais_catalogo_especie_idx on public.animais (especie)
    where status_publicacao = 'publicado'
      and status_adocao = 'disponivel' and not oculto_admin;
create index animais_catalogo_porte_idx on public.animais (porte)
    where status_publicacao = 'publicado'
      and status_adocao = 'disponivel' and not oculto_admin;
create index animais_catalogo_distrito_idx on public.animais (distrito)
    where status_publicacao = 'publicado'
      and status_adocao = 'disponivel' and not oculto_admin;

create function public.animais_atualizar_data()
returns trigger
language plpgsql
security invoker
set search_path = ''
as $$
begin
    new.atualizado_em := statement_timestamp();
    return new;
end;
$$;

revoke all on function public.animais_atualizar_data()
    from public, anon, authenticated;

create trigger animais_atualizar_data
    before update on public.animais
    for each row execute function public.animais_atualizar_data();

alter table public.animais enable row level security;

-- Remove inclusive grants amplos herdados dos privilégios padrão do projeto.
-- Não conceder INSERT/UPDATE de tabela: isso anularia a restrição por coluna.
revoke all on table public.animais from public, anon, authenticated;
grant usage on schema public to anon, authenticated;
grant select on table public.animais to anon, authenticated;
grant delete on table public.animais to authenticated;
grant insert (
    nome, especie, especie_outro, sexo, idade_aproximada, porte,
    descricao, distrito, bairro, whatsapp, email_contato
) on public.animais to authenticated;
grant update (
    nome, especie, especie_outro, sexo, idade_aproximada, porte,
    descricao, distrito, bairro, whatsapp, email_contato, status_adocao
) on public.animais to authenticated;

create policy animais_leitura_publica on public.animais
    for select to anon, authenticated
    using (
        status_publicacao = 'publicado'
        and status_adocao = 'disponivel' and not oculto_admin
    );

create policy animais_leitura_proprios on public.animais
    for select to authenticated
    using (usuario_id = (select auth.uid()));

create policy animais_insercao_propria on public.animais
    for insert to authenticated
    with check (
        usuario_id = (select auth.uid())
        and status_publicacao = 'rascunho'
        and status_adocao = 'disponivel' and not oculto_admin
    );

create policy animais_edicao_propria on public.animais
    for update to authenticated
    using (usuario_id = (select auth.uid()))
    with check (usuario_id = (select auth.uid()));

create policy animais_exclusao_propria on public.animais
    for delete to authenticated
    using (usuario_id = (select auth.uid()));

commit;
