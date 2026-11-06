-- ROTEIRO MANUAL: execução integral pendente. Ler ../README.md antes de usar.
-- Cenários aprovados em blocos independentes com ROLLBACK em adotae-testes.
-- Novas execuções somente em ambiente descartável e com autorização.
-- Mesma conexão em todos os passos; instruções com erro esperado são individuais.
-- Pré-requisitos: migração aplicada e dois usuários existentes no Supabase Auth.
-- Substituir os dois UUIDs abaixo pelos IDs reais de A e B (não usar credenciais).
begin;
select set_config('adotae.teste_usuario_a', 'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa', true);
select set_config('adotae.teste_usuario_b', 'bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb', true);

-- Operador privilegiado: conferir dois IDs distintos e existentes antes de seguir.
select id from auth.users where id in (
    current_setting('adotae.teste_usuario_a')::uuid,
    current_setting('adotae.teste_usuario_b')::uuid
); -- Esperado: 2 linhas. Não criar/modificar auth.users por este roteiro.

-- Fixtures: somente o operador privilegiado cria estados não acessíveis a clientes.
insert into public.animais (
    usuario_id, nome, especie, sexo, idade_aproximada, porte, descricao,
    distrito, email_contato, status_publicacao, status_adocao, oculto_admin
)
select current_setting(case when dono = 'A' then 'adotae.teste_usuario_a'
                            else 'adotae.teste_usuario_b' end)::uuid,
       nome, 'Gato', 'Fêmea', '1 ano', 'Pequeno', 'Fixture fictícia de RLS',
       'Grussaí', 'contato@example.org', publicacao, adocao, oculto
from (values
    ('A', 'RLS A rascunho', 'rascunho', 'disponivel', false),
    ('A', 'RLS A publico', 'publicado', 'disponivel', false),
    ('A', 'RLS A adotado', 'publicado', 'adotado', false),
    ('A', 'RLS A oculto', 'publicado', 'disponivel', true),
    ('B', 'RLS B rascunho', 'rascunho', 'disponivel', false),
    ('B', 'RLS B publico', 'publicado', 'disponivel', false)
) as casos(dono, nome, publicacao, adocao, oculto);

-- 1. Visitante sem identidade: apenas dois anúncios públicos.
set local role anon;
select set_config('request.jwt.claim.sub', '', true);
select set_config('request.jwt.claims', '{"role":"anon"}', true);
select auth.uid(); -- NULL
select nome from public.animais where nome like 'RLS %' order by nome;
-- Exatamente: RLS A publico, RLS B publico. Nenhum rascunho, adotado ou oculto.
savepoint negado;
delete from public.animais where nome = 'RLS A publico'; -- ERRO 42501
rollback to savepoint negado;
savepoint negado;
update public.animais set descricao = 'invasão' where nome = 'RLS A publico'; -- ERRO 42501
rollback to savepoint negado;
savepoint negado;
insert into public.animais (nome) values ('Visitante'); -- ERRO 42501
rollback to savepoint negado;

-- 2. Identidade A, simulada apenas pelo operador privilegiado de testes.
reset role;
select set_config('request.jwt.claim.sub', current_setting('adotae.teste_usuario_a'), true);
select set_config('request.jwt.claims', json_build_object(
    'sub', current_setting('adotae.teste_usuario_a'), 'role', 'authenticated'
)::text, true);
set local role authenticated;
select current_user, auth.uid(); -- authenticated, ID de A
select nome from public.animais where nome like 'RLS %' order by nome;
-- Cinco: os quatro de A (todos os estados) e RLS B publico.

-- Inserção normal sem contato e sem campos protegidos: 1 linha, identidade de A.
insert into public.animais (
    nome, especie, sexo, idade_aproximada, porte, descricao, distrito
) values ('RLS A novo', 'Cachorro', 'Macho', '6 meses', 'Médio', 'Rascunho', 'Atafona')
returning id, usuario_id, status_publicacao, status_adocao, oculto_admin,
          criado_em, atualizado_em;
-- UUID, A, rascunho, disponivel, false e datas preenchidas.
update public.animais set descricao = 'Editado pelo dono'
    where nome = 'RLS A novo' returning criado_em, atualizado_em; -- 1 linha
-- criado_em preservado; atualizado_em passa ao horário da instrução UPDATE.

-- Outro proprietário: nenhuma alteração, inclusive quando o anúncio é público.
update public.animais set descricao = 'invasão' where nome = 'RLS B publico'; -- UPDATE 0
delete from public.animais where nome like 'RLS B %'; -- DELETE 0

-- Cada tentativa protegida: erro 42501 e recuperação antes de continuar.
savepoint negado;
insert into public.animais (usuario_id, nome)
values (current_setting('adotae.teste_usuario_b')::uuid, 'Forjado'); -- ERRO
rollback to savepoint negado;
savepoint negado;
update public.animais set usuario_id = current_setting('adotae.teste_usuario_b')::uuid
    where nome = 'RLS A novo'; -- ERRO
rollback to savepoint negado;
savepoint negado;
update public.animais set oculto_admin = false where nome = 'RLS A oculto'; -- ERRO
rollback to savepoint negado;
savepoint negado;
insert into public.animais (nome, oculto_admin) values ('Ocultação forjada', true); -- ERRO
rollback to savepoint negado;
savepoint negado;
insert into public.animais (nome, status_publicacao) values ('Publicação direta', 'publicado'); -- ERRO
rollback to savepoint negado;
savepoint negado;
update public.animais set status_publicacao = 'publicado' where nome = 'RLS A novo'; -- ERRO
rollback to savepoint negado;
savepoint negado;
update public.animais set criado_em = now(), atualizado_em = now()
    where nome = 'RLS A novo'; -- ERRO
rollback to savepoint negado;
savepoint negado;
update public.animais set id = gen_random_uuid() where nome = 'RLS A novo'; -- ERRO
rollback to savepoint negado;
savepoint negado;
truncate public.animais; -- ERRO: TRUNCATE não é protegido por RLS; exige privilégio SQL.
rollback to savepoint negado;

-- CHECKs (23514), executados com privilégios de usuário comum.
savepoint invalido;
update public.animais set distrito = 'Campos dos Goytacazes' where nome = 'RLS A novo';
rollback to savepoint invalido;
savepoint invalido;
update public.animais set especie = 'Dragão' where nome = 'RLS A novo';
rollback to savepoint invalido;
savepoint invalido;
update public.animais set especie = 'Outro animal doméstico', especie_outro = null
    where nome = 'RLS A novo';
rollback to savepoint invalido;
savepoint invalido;
update public.animais set porte = 'Gigante' where nome = 'RLS A novo';
rollback to savepoint invalido;
savepoint invalido;
update public.animais set sexo = 'inválido' where nome = 'RLS A novo';
rollback to savepoint invalido;
savepoint invalido;
update public.animais set nome = E' \t\n' where nome = 'RLS A novo';
rollback to savepoint invalido;
savepoint invalido;
update public.animais set whatsapp = '+55 (22) 99999-9999' where nome = 'RLS A novo';
rollback to savepoint invalido;
savepoint invalido;
update public.animais set email_contato = 'sem-arroba' where nome = 'RLS A novo';
rollback to savepoint invalido;
savepoint invalido;
update public.animais set email_contato = null, whatsapp = null
    where nome = 'RLS A publico'; -- Contato obrigatório em publicado, até na edição.
rollback to savepoint invalido;
savepoint invalido;
update public.animais set status_adocao = 'inválido' where nome = 'RLS A novo';
rollback to savepoint invalido;

-- Limites exatos válidos, inclusive com caracteres multibyte (não contar bytes).
-- SAVEPOINT também desfaz o nome alterado para preservar as fixtures anteriores.
savepoint limites_validos;
update public.animais set nome = repeat('á', 100), descricao = repeat('ç', 2000),
    idade_aproximada = repeat('ã', 50), bairro = repeat('é', 100),
    especie = 'Outro animal doméstico', especie_outro = repeat('ó', 60)
    where nome = 'RLS A novo'
    returning char_length(nome), char_length(descricao), char_length(idade_aproximada),
              char_length(bairro), char_length(especie_outro);
-- UPDATE 1; comprimentos: 100, 2000, 50, 100, 60.
rollback to savepoint limites_validos;

-- Um caractere acima de cada limite: ERRO 23514 em cada UPDATE.
savepoint invalido;
update public.animais set nome = repeat('á', 101) where nome = 'RLS A novo';
rollback to savepoint invalido;
savepoint invalido;
update public.animais set descricao = repeat('ç', 2001) where nome = 'RLS A novo';
rollback to savepoint invalido;
savepoint invalido;
update public.animais set idade_aproximada = repeat('ã', 51) where nome = 'RLS A novo';
rollback to savepoint invalido;
savepoint invalido;
update public.animais set bairro = repeat('é', 101) where nome = 'RLS A novo';
rollback to savepoint invalido;
savepoint invalido;
update public.animais set especie = 'Outro animal doméstico', especie_outro = repeat('ó', 61)
    where nome = 'RLS A novo';
rollback to savepoint invalido;

-- Textos vazios/espaços continuam inválidos: ERRO 23514 em cada UPDATE.
savepoint invalido;
update public.animais set nome = '' where nome = 'RLS A novo';
rollback to savepoint invalido;
savepoint invalido;
update public.animais set descricao = '' where nome = 'RLS A novo';
rollback to savepoint invalido;
savepoint invalido;
update public.animais set descricao = E' \t\n' where nome = 'RLS A novo';
rollback to savepoint invalido;
savepoint invalido;
update public.animais set idade_aproximada = '' where nome = 'RLS A novo';
rollback to savepoint invalido;
savepoint invalido;
update public.animais set idade_aproximada = E' \t\n' where nome = 'RLS A novo';
rollback to savepoint invalido;
savepoint invalido;
update public.animais set bairro = '' where nome = 'RLS A novo';
rollback to savepoint invalido;
savepoint invalido;
update public.animais set bairro = E' \t\n' where nome = 'RLS A novo';
rollback to savepoint invalido;
savepoint invalido;
update public.animais set especie = 'Outro animal doméstico', especie_outro = ''
    where nome = 'RLS A novo';
rollback to savepoint invalido;
savepoint invalido;
update public.animais set especie = 'Outro animal doméstico', especie_outro = E' \t\n'
    where nome = 'RLS A novo';
rollback to savepoint invalido;

-- Rascunho não permite campo principal ausente: ERRO 23502.
savepoint invalido;
update public.animais set descricao = null where nome = 'RLS A novo';
rollback to savepoint invalido;
savepoint invalido;
insert into public.animais (nome) values ('Rascunho incompleto'); -- ERRO 23502
rollback to savepoint invalido;

-- Espécie comum com complemento preenchido: ERRO 23514.
savepoint invalido;
update public.animais set especie_outro = 'Coelho' where nome = 'RLS A novo';
rollback to savepoint invalido;
-- Complemento válido na categoria Outro: UPDATE 1.
update public.animais set especie = 'Outro animal doméstico', especie_outro = 'Coelho'
    where nome = 'RLS A novo';
savepoint invalido;
update public.animais set especie = 'Gato' where nome = 'RLS A novo'; -- ERRO 23514
rollback to savepoint invalido;
-- Troca válida remove o complemento; bairro e contatos seguem opcionais: UPDATE 1.
update public.animais set especie = 'Gato', especie_outro = null, bairro = null,
    whatsapp = null, email_contato = null where nome = 'RLS A novo'
    returning especie, especie_outro, bairro, status_publicacao;
-- Gato, NULL, NULL, rascunho.

-- Estados e contatos válidos.
update public.animais set especie = 'Outro animal doméstico', especie_outro = 'Coelho',
    whatsapp = '5522999999999', email_contato = 'outro@example.org'
    where nome = 'RLS A novo'; -- UPDATE 1; ambos os contatos aceitos.
update public.animais set descricao = 'Dono edita sem reativar'
    where nome = 'RLS A oculto' returning oculto_admin; -- true
update public.animais set status_adocao = 'adotado' where nome = 'RLS A publico'; -- UPDATE 1
-- O dono ainda consulta seu anúncio adotado.
select nome from public.animais where nome = 'RLS A publico'; -- 1 linha
delete from public.animais where nome = 'RLS A novo'; -- DELETE 1

-- 3. B: simetria de isolamento; A adotado/oculto/rascunho não aparecem.
reset role;
select set_config('request.jwt.claim.sub', current_setting('adotae.teste_usuario_b'), true);
select set_config('request.jwt.claims', json_build_object(
    'sub', current_setting('adotae.teste_usuario_b'), 'role', 'authenticated'
)::text, true);
set local role authenticated;
select nome from public.animais where nome like 'RLS %' order by nome;
-- Exatamente: RLS B publico e RLS B rascunho.
update public.animais set descricao = 'invasão' where nome like 'RLS A %'; -- UPDATE 0
delete from public.animais where nome like 'RLS A %'; -- DELETE 0
update public.animais set descricao = 'B edita o próprio' where nome = 'RLS B rascunho'; -- UPDATE 1

-- 4. authenticated sem sub não pode inserir, nem ler rascunhos como dono.
reset role;
select set_config('request.jwt.claim.sub', '', true);
select set_config('request.jwt.claims', '{"role":"authenticated"}', true);
set local role authenticated;
select nome from public.animais where nome like 'RLS %'; -- Apenas RLS B publico.
savepoint negado;
insert into public.animais (nome, especie, sexo, idade_aproximada, porte, descricao, distrito)
values ('Sem identidade', 'Gato', 'Fêmea', '1 ano', 'Pequeno', 'Teste', 'Grussaí');
-- ERRO de RLS (42501) ou NOT NULL (23502); nenhuma inserção.
rollback to savepoint negado;

-- 5. Visitante após adoção: anúncio deixa de ser público imediatamente.
reset role;
set local role anon;
select nome from public.animais where nome like 'RLS %'; -- Apenas RLS B publico.

-- 6. Inspeção pelo operador: ENABLE RLS, grants e CHECK independente de RLS.
reset role;
select relrowsecurity from pg_class where oid = 'public.animais'::regclass; -- true
select policyname, roles, cmd, qual, with_check from pg_policies
    where schemaname = 'public' and tablename = 'animais'; -- 5 políticas
select grantee, privilege_type from information_schema.table_privileges
    where table_schema = 'public' and table_name = 'animais';
select grantee, column_name, privilege_type from information_schema.column_privileges
    where table_schema = 'public' and table_name = 'animais'
      and grantee in ('anon', 'authenticated') order by grantee, column_name, privilege_type;
-- Não pode haver INSERT/UPDATE de tabela para anon/authenticated ou PUBLIC.
-- Nenhuma escrita nas colunas protegidas, nem privilégios herdados mais amplos.
select has_column_privilege('authenticated', 'public.animais', 'usuario_id', 'INSERT'),
       has_column_privilege('authenticated', 'public.animais', 'usuario_id', 'UPDATE'),
       has_column_privilege('authenticated', 'public.animais', 'oculto_admin', 'UPDATE'),
       has_column_privilege('authenticated', 'public.animais', 'status_publicacao', 'INSERT'),
       has_column_privilege('authenticated', 'public.animais', 'status_publicacao', 'UPDATE');
-- Todos false. Os testes não podem ser feitos só como postgres (bypass RLS).
savepoint invalido;
update public.animais set status_publicacao = 'publicado', email_contato = null,
    whatsapp = null where nome = 'RLS A rascunho'; -- ERRO 23514, mesmo privilegiado.
rollback to savepoint invalido;

-- Limpeza: descarta TODAS as fixtures e alterações, não a migração já aplicada.
rollback;
