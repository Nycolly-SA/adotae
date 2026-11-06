# Banco de dados — etapa SQL

O arquivo [202611010001_create_animais.sql](migrations/202611010001_create_animais.sql)
cria `public.animais` no Supabase PostgreSQL. A migração foi aplicada em
`adotae-testes` e no projeto principal. O catálogo não está integrado ao banco.
Os resultados remotos abaixo vêm do histórico informado; nesta revisão não
foram executados SQL nem acessos ao Supabase.

## Estado dos ambientes

| Verificação | `adotae-testes` | Principal |
| --- | --- | --- |
| Migração | Aplicada | Aplicada; 18 colunas, RLS, cinco políticas, índices, função e trigger confirmados |
| Segurança SQL | Cenários manuais de RLS, privilégios e constraints aprovados; auditoria de 11 verificações | Auditoria posterior à correção dos privilégios: 10 de 10 aprovadas |
| Data API com chave publicável | Testes reais com dois usuários distintos e acesso anônimo aprovados | Leitura sem sessão aprovada, sem erro e com zero registros |
| Escrita autenticada pela API | Operações próprias, isolamento e bloqueios aprovados | Ainda não testada |
| Registros temporários | Limpeza concluída | Leitura relatada não criou registros |

No principal, a Data API está ativada, com schema `public` e tabela
`public.animais` expostos; a exposição automática de novas tabelas está desativada.
A leitura vazia confirma acesso ao endpoint, mas não comprova visibilidade de
anúncios publicados nem isolamento ou escrita com sessões reais nesse ambiente.

**Cuidado ao habilitar tabelas na Data API:** a interface pode alterar privilégios
SQL. No principal, concedeu privilégios excessivos, corrigidos manualmente pela
restauração dos `GRANT` restritos da migração antes da auditoria de 10 verificações.
Após mudanças de exposição, auditar novamente RLS e privilégios efetivos de tabela
e coluna. RLS não substitui bloqueios por coluna. Não reaplicar a migração para
corrigir permissões.

## Decisões de modelagem

- UUID gerado pelo PostgreSQL; proprietário obrigatório, com FK para
  `auth.users(id)` e valor padrão `auth.uid()`. A aplicação omite esse campo.
- `especie`, `porte` e `distrito` usam exatamente os valores de `OPCOES_FILTROS`
  em `run.py`; `sexo` admite `Macho` e `Fêmea`, como os dados demonstrativos.
  O parâmetro GET `tipo` corresponde à coluna `especie`. Os identificadores
  inteiros demonstrativos não são migrados.
- `idade_aproximada` é texto, compatível com “6 meses” e “2 anos”; corresponde
  ao campo demonstrativo `idade`. Não foram inventadas faixas de idade.
- Nome, espécie, sexo, idade, porte, descrição e distrito são obrigatórios
  inclusive no rascunho: decisão confirmada, sem salvamento parcialmente
  preenchido. Contatos e fotos podem ser adicionados posteriormente, antes da
  publicação; fotos ainda não são implementadas. Texto obrigatório ou opcional
  preenchido não pode ser vazio nem conter apenas espaços. Campos opcionais
  ausentes devem ser `NULL`.
- Limites máximos, medidos em caracteres por `char_length`: `nome` 100,
  `descricao` 2.000, `idade_aproximada` 50, `bairro` 100 e `especie_outro` 60.
  Os limites incluem espaços e se aplicam tanto a rascunhos como a publicados.
- `especie_outro` identifica o animal quando escolhida a categoria
  `Outro animal doméstico`: é obrigatório, não vazio e limitado a 60 caracteres.
  Para qualquer outra espécie, deve ser `NULL`. Ao trocar de categoria, atualizar
  os dois campos na mesma instrução quando necessário. SQL não verifica legalidade
  da espécie; orientação e validação apropriadas ficam para o cadastro futuro.
- Município fixo São João da Barra/RJ, sem coluna redundante. `bairro` é texto
  opcional, sem endereço residencial completo e sem inventar lista de bairros.
- Contatos são próprios do anúncio, escolhidos para divulgação; nunca são
  copiados automaticamente de `auth.users`. Ambos podem estar ausentes no
  rascunho; um `CHECK` exige pelo menos um em qualquer linha publicada.
  WhatsApp usa apenas dígitos, com código do país e sem `+`, entre 8 e 15 dígitos
  (exemplo fictício: `5522999999999`). O e-mail tem verificação sintática básica;
  as expressões não comprovam existência, titularidade ou entrega.
- Categorias e estados usam `text` com `CHECK`, sem tipos enum adicionais.
  Valores iniciais: `rascunho`, `disponivel` e `oculto_admin = false`.
- Datas usam `timestamptz`; trigger atualiza `atualizado_em` em cada UPDATE,
  inclusive se os valores não mudarem. Datas e ID não são graváveis pelo cliente.
  A função usa privilégios do chamador, `search_path` vazio e não é uma RPC.
- Índice de proprietário atende consultas próprias e a FK. Três índices parciais
  atendem os filtros públicos independentes; avaliar com dados reais antes de
  adicionar outros índices ou otimizar.
- A FK mantém `NO ACTION`: excluir uma conta com anúncios será bloqueado.
  Exclusão em cascata e retenção de dados precisam de decisão futura.

## Permissões e limites

As políticas SELECT são permissivas e se somam: visitantes veem somente
publicados, disponíveis e não ocultos; autenticados veem também todos os próprios.
UPDATE e DELETE têm políticas exclusivas de propriedade, mesmo quando um anúncio
de outra pessoa é legível publicamente. O proprietário pode editar/excluir um
anúncio oculto, mas não remover a ocultação.

`anon` recebe SELECT. `authenticated` recebe SELECT, DELETE e INSERT/UPDATE
somente nas colunas listadas no SQL. Não recebe TRUNCATE, REFERENCES ou TRIGGER.
`usuario_id`, `oculto_admin`, `status_publicacao`, ID e datas ficam fora dos grants
de escrita; `status_adocao` só pode ser editado, não escolhido na inserção.
A identidade da inserção vem do JWT validado pelo Supabase via `auth.uid()`,
nunca de parâmetro Flask, metadados editáveis ou um UUID enviado pelo formulário.

**Publicação está bloqueada para usuários comuns, tanto em INSERT como UPDATE.**
Não basta enviar `status_publicacao = 'rascunho'`: o cliente deve omitir a coluna,
assim como as demais colunas sem permissão de escrita. RLS também exige rascunho,
disponível e não oculto na inserção. Não há mecanismo de publicação ou de
administração nesta etapa. Uma futura migração deve validar foto obrigatória e
confirmação de e-mail antes de liberar publicação; não basta ampliar um grant.

SELECT atualmente inclui todas as colunas das linhas autorizadas, inclusive
`usuario_id` (UUID), contatos e datas. Nenhum e-mail da conta ou dado de
`auth.users` é exposto por essa FK. Revisar a necessidade de esconder o UUID do
proprietário antes de uma API pública definitiva; RLS controla linhas, não colunas.

O SQL Editor, proprietário da tabela e papéis com BYPASSRLS podem ultrapassar
RLS; não representam um usuário da aplicação. Não usar `service_role` na
aplicação. A futura integração deve usar chave publicável e JWT do usuário.
Não há papel administrativo nem autorização baseada em `user_metadata`.

## Validação SQL no projeto de testes e seus limites

Conforme o relato dos testes, foram aprovados no SQL Editor de `adotae-testes`,
com dois usuários fictícios e simulação dos papéis `anon` e `authenticated`:

- Leitura pública e leitura de todos os estados pelo proprietário.
- Inserção de rascunhos com proprietário e valores padrão corretos.
- Bloqueio de edição/exclusão de terceiros e edição/exclusão dos próprios anúncios.
- Privilégios e tentativas de escrita em campos protegidos.
- Constraints `CHECK`, contatos obrigatórios na publicação e formatos inválidos.
- Bloqueio de operações de escrita para visitantes e usuários sem identidade.
- Visibilidade de anúncios adotados e ocultos.
- Valores exatamente nos limites permitidos e rejeição dos excessos.
- Auditoria de 11 verificações de RLS e privilégios.

Os testes foram executados em **blocos SQL independentes com `ROLLBACK`**.
**Não houve execução integral de `tests/animais_rls.sql`.** A auditoria de 11
verificações faz parte dos testes manuais relatados; não é uma contagem de
asserções automatizadas do arquivo. Nenhum teste SQL foi repetido nesta revisão.
Nesta revisão, passaram 74 testes automatizados locais (70 no histórico anterior),
incluindo
[os testes offline do roteiro Python](../tests/test_validacao_data_api.py).
Esses testes não validam SQL, RLS instalada ou acesso real pela Data API.

Também foram aprovados testes reais pela Data API em `adotae-testes`, com chave
publicável e sessões de dois usuários distintos: operações próprias, isolamento
entre proprietários, bloqueios de operações indevidas e acesso anônimo.
A limpeza temporária foi concluída. Isso não equivale à execução integral do
roteiro SQL nem à validação de escrita no principal.

Visitantes e `authenticated` sem identidade ainda podem ler as linhas públicas;
o bloqueio relatado não significa proibição dessa leitura prevista nas políticas.

## Coerência e uso do roteiro

O [roteiro SQL](tests/animais_rls.sql) cobre dois usuários e visitantes, com
fixtures de rascunho, publicação, adoção e ocultação, além dos limites exatos de
caracteres, excesso de um caractere e consistência da espécie. A revisão estática
encontrou coerência entre seus resultados esperados e a migração. Ele é um roteiro
manual, com conferência dos resultados pelo operador, sem asserções automáticas.

Para uma execução futura autorizada do roteiro completo, usar ambiente descartável
Supabase com Auth e papéis `anon`/`authenticated`, com a migração já aplicada uma
única vez, e seguir os comentários do roteiro. Não reaplicar em `adotae-testes`.
A migração usa transação e falha se os objetos já existirem;
não é um script de sincronização nem usa `IF NOT EXISTS` para ocultar divergências.
Não é preciso instalar CLI, dependências ou criar infraestrutura para revisar.

O roteiro deve permanecer na mesma conexão, com autocommit sem interferir no
`BEGIN`. Erros esperados usam SAVEPOINT; execute cada instrução separadamente
e recupere com `ROLLBACK TO SAVEPOINT`. Não executar o arquivo inteiro em um
executor que pare no primeiro erro. Sempre terminar com `ROLLBACK`, inclusive
ao interromper. As fixtures publicadas são criadas pelo operador somente para
testar leitura; isso não libera publicação para clientes.

`SET LOCAL ROLE` e os claims simulam o contexto da Data API: não testam assinatura,
expiração de JWT ou confirmação de e-mail. Apenas o operador de testes pode
simular claims; a aplicação não deve abrir conexão SQL privilegiada nem aceitar
claims enviados pelo cliente. A validação complementar com chave publicável,
sessões reais de A/B e visitante já foi aprovada em `adotae-testes`.

## Resultado da revisão estática

- Não foi identificado defeito crítico na migração por leitura estática. As duas
  políticas SELECT se somam, enquanto UPDATE/DELETE continuam limitados ao dono;
  `WITH CHECK` preserva a propriedade na edição. Sem `auth.uid()`, não há acesso
  como proprietário. A leitura pública continua permitida.
- Os grants por coluna complementam RLS: propriedade, publicação, ocultação, ID
  e datas não são graváveis pelos clientes. A revogação dos grants amplos evita
  que permissões de tabela anulem essas restrições. Papéis herdados e permissões
  efetivas dependem da configuração de cada projeto e exigem conferência no destino.
- `NOT NULL` e `CHECK` são coerentes com os rascunhos completos e contatos opcionais
  somente antes da publicação. As verificações opcionais aceitam `NULL`, mas não
  texto vazio. Não há validação de foto ou e-mail confirmado nesta etapa; por isso,
  a publicação permanece bloqueada para clientes.
- Os índices parciais têm o mesmo predicado da leitura pública; o índice de
  `usuario_id` atende acesso próprio e verificação da FK. Ganhos de desempenho
  não foram medidos. A trigger preserva `criado_em` e preenche `atualizado_em`
  com o horário da instrução, sem elevar privilégios.
- O roteiro corresponde às regras atuais, mas depende de fixtures isoladas,
  conexão contínua e recuperação dos erros esperados. Os blocos independentes
  usados manualmente precisam preparar seu próprio contexto e fixtures; não basta
  executar trechos arbitrários do arquivo. A revisão não certifica sua execução integral.

## Pendências por prioridade

1. **Alta — validar escrita autenticada pela Data API no principal.** A leitura
   anônima e a auditoria não substituem testes com dois usuários distintos para
   operações próprias, isolamento e campos protegidos. O roteiro Python deste
   repositório é exclusivo de `adotae-testes`, não do principal.
2. **Média — completar cenários da API não cobertos pelo roteiro Python.** JWT
   inválido/expirado, leitura de anúncios publicados e demais constraints pela
   API não são cenários desse script. Os testes SQL manuais de constraints e
   leitura pública foram aprovados no projeto de testes, sem equivaler a esses
   cenários pela API. Não há resultado remoto adicional registrado aqui.
3. **Média — decidir sobre a exposição de `usuario_id` antes da API pública
   definitiva.** A leitura atual permite correlacionar anúncios do mesmo proprietário.
   Isso já está previsto no SELECT de todas as colunas e não expõe `auth.users`,
   mas a necessidade desse identificador público ainda deve ser avaliada. Nenhum
   grant ou esquema foi alterado nesta revisão.
4. **Baixa — execução integral controlada do roteiro permanece pendente.** Caso
   realizada, conferir cada resultado na mesma conexão e recuperar cada erro com
   SAVEPOINT, terminando em `ROLLBACK`; enviar o arquivo inteiro ao SQL Editor
   não equivale a essa execução controlada. Os cenários manuais aprovados continuam
   registrados separadamente.

Essas pendências não exigem novas funcionalidades para concluir a revisão SQL.
Fotos, publicação e administração pertencem às próximas etapas e não são condições
para manter esta base já aplicada, desde que a publicação continue bloqueada.

Pendências de integração futura:

- Validar formatos de contato na interface.
- Projetar fotos/Storage, verificação de e-mail na publicação, autorização
  administrativa confiável e política de exclusão de contas posteriormente.
- O escopo local ainda descreve provedores e autenticação como pendentes;
  README e código já usam Supabase Auth. Esta preparação adota Supabase conforme
  a solicitação, sem reescrever esse documento local ignorado pelo Git.

Referências: [RLS](https://supabase.com/docs/guides/database/postgres/row-level-security),
[privilégios por coluna](https://supabase.com/docs/guides/database/postgres/column-level-security)
e [privilégios PostgreSQL](https://www.postgresql.org/docs/17/ddl-priv.html).
Para novas validações pela API, consultar também
[chaves e papéis da API](https://supabase.com/docs/guides/getting-started/api-keys).

## Roteiro Python para validação real da Data API

O script [tests/validar_data_api.py](tests/validar_data_api.py) acompanha a
validação real aprovada em `adotae-testes`, conforme o histórico informado.
Não foi executado nesta revisão. Usa o SDK Python já instalado, sem importar
Flask, carregar `.env`, abrir conexão SQL ou usar privilégios administrativos.
Os testes locais não validam as políticas instaladas. As instruções abaixo
servem para futuras repetições manuais no projeto de testes.

### Preparação e execução local

1. No painel, abra exclusivamente **adotae-testes**. A migração deve estar aplicada;
   não a reaplique para executar este roteiro. A Data API precisa expor `public.animais`.
2. Tenha duas contas fictícias distintas de Supabase Auth nesse projeto, A e B,
   com e-mails confirmados e senha. O script não cadastra nem altera contas.
3. Confira no painel desse projeto a URL, a chave **publicável** `sb_publishable_...`
   e a **Project Reference**. Chaves secretas, `service_role` e chaves JWT legadas
   são recusadas. Use um terminal local sem captura/transcrição de credenciais.
4. Na raiz do repositório, configure somente as variáveis abaixo no PowerShell
   e execute manualmente com o Python do ambiente já existente:

   ```powershell
   $env:ADOTAE_TEST_SUPABASE_URL = "https://<referencia-de-adotae-testes>.supabase.co"
   $env:ADOTAE_TEST_SUPABASE_PUBLISHABLE_KEY = "sb_publishable_<chave-do-projeto-de-testes>"
   .\.venv\Scripts\python.exe supabase/tests/validar_data_api.py
   ```

   Substitua os placeholders pelos valores de **adotae-testes**. Não altere
   `SUPABASE_URL`, `SUPABASE_PUBLISHABLE_KEY` nem o `.env` da aplicação.
   O roteiro não usa essas variáveis como fallback e recusa um destino igual
   a `SUPABASE_URL` quando essa variável já estiver no ambiente do processo.

5. Antes de qualquer conexão, o roteiro pede `adotae-testes <Project Reference>`.
   Copie a referência conferida **independentemente no painel de adotae-testes**,
   não apenas da URL digitada. Essa confirmação vincula o destino ao projeto
   escolhido: a chave publicável não permite consultar administrativamente o
   nome do projeto. Depois, informe os e-mails e senhas de A e B nos prompts.
   Ambos ficam visíveis enquanto você digita, conforme solicitado para este
   teste local. Não informe credenciais na linha de comando ou em variáveis de ambiente.

### O que é conferido

- Clientes e sessões independentes de A e B, obtidas por
  [`sign_in_with_password()`](https://supabase.com/docs/reference/python/auth-signinwithpassword),
  com identidade confirmada pelo servidor Auth; terceiro cliente sem sessão.
- Criação de rascunhos completos e fictícios por A/B, com `usuario_id` preenchido
  pelo banco e estados iniciais corretos, seguida de consulta pelo proprietário.
- A não lê/edita/exclui o rascunho de B e vice-versa. Após cada tentativa,
  o proprietário consulta novamente a linha e confere seu estado integral.
- Visitante não lê os rascunhos nem insere anúncios; ambos os proprietários
  conseguem editar a descrição dos próprios registros.
- Ambos os usuários têm `status_publicacao = 'publicado'` recusado no UPDATE
  e no INSERT. O contato de teste usa o domínio reservado `example.invalid`,
  evitando que ausência de contato mascare o bloqueio de publicação.

As escritas solicitam os registros afetados. Uma resposta sem exceção só passa
quando os dados e a leitura posterior correspondem ao esperado. Operações RLS
podem retornar zero linhas; erros só contam como bloqueio se o código for
`42501` (permissão/RLS). Rede, sessão inválida, constraints e outros erros
reprovam/interrompem o roteiro, sem imprimir respostas sensíveis ou tracebacks.
Não há teste de JWT adulterado/expirado, leitura de anúncios publicados ou
demais constraints neste script; essas verificações continuam separadas.

### Se a autenticação falhar

O roteiro diferencia falha no login de falha na consulta de identidade da sessão.
Exibe mensagens controladas para credenciais não reconhecidas, e-mail sem
confirmação, limite de tentativas, indisponibilidade e problemas de conexão.
Quando disponível, mostra apenas o status HTTP e um código Auth de uma lista
conhecida, nunca a mensagem bruta do servidor, o e-mail, a senha ou tokens.
Os códigos seguem a [referência de erros Auth](https://supabase.com/docs/guides/auth/debugging/error-codes).

`invalid_credentials` pede conferir a conta e sua senha **em adotae-testes**.
A e B são rótulos: informe nos prompts os e-mails e senhas de duas contas que
já existam em **Authentication → Users** desse projeto. Não basta inventar
um e-mail durante o teste, ter uma conta somente no projeto principal, ou ter
simulado um UUID nos testes SQL. O script não cria usuários.

Se a execução parar durante a autenticação, nenhuma inserção foi tentada e
o roteiro informa que não há anúncios desta execução para limpar. É possível
compartilhar a nova mensagem controlada para diagnóstico, sem enviar credenciais.

### Limpeza e resultado

Antes das inserções, o roteiro imprime os cinco nomes exclusivos `TESTE-RLS-...`
das possíveis fixtures da execução, sem UUIDs de usuário ou credenciais.
Guarde esses nomes até conferir o resultado. No fluxo esperado, só dois anúncios
são criados; os outros nomes identificam tentativas que devem ser recusadas.

A limpeza ocorre em `finally`, inclusive após falha ou Ctrl+C: cada usuário
procura os nomes exatos desta execução filtrando pelo próprio `usuario_id`,
exclui seus registros e confirma a ausência deles. A busca por nome recupera
também inserções que chegaram ao servidor mas perderam a resposta. O script
encerra somente suas sessões locais; não faz logout global das contas.
O e-mail e a senha ficam visíveis no terminal durante a entrada. O script não
grava credenciais em disco nem as repete nos resultados; tokens nunca são
exibidos. As sessões existem apenas em memória durante a execução.

Se a limpeza falhar, aparecem os nomes exatos e os IDs recuperados que precisam
de conferência/remoção manual **somente em adotae-testes**. Uma execução reprovada
após o início das escritas também pede conferência desses nomes no painel: uma configuração incorreta pode
criar registros invisíveis por RLS ou com proprietário inesperado, que os usuários
não conseguem remover. Encerramento forçado do processo ou queda de energia não
garantem execução de `finally`; nesse caso, use os nomes impressos anteriormente.
As chamadas da Data API não compartilham uma transação com rollback.

O processo retorna **0** apenas se todos os cenários e a limpeza forem aprovados;
retorna **1** para falha, interrupção ou encerramento de sessão não confirmado.
Não interprete um resultado local como validação do projeto principal.

Após o teste, remova a configuração temporária do terminal:

```powershell
Remove-Item Env:ADOTAE_TEST_SUPABASE_URL
Remove-Item Env:ADOTAE_TEST_SUPABASE_PUBLISHABLE_KEY
```

Os testes **offline**, sem executar o roteiro real, podem ser rodados com:

```powershell
.\.venv\Scripts\python.exe -m unittest tests.test_validacao_data_api
```
