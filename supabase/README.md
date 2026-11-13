# Banco de dados — etapa SQL

## Sexo não identificado — aplicação remota pendente

A migração [202611130001_sexo_nao_identificado.sql](migrations/202611130001_sexo_nao_identificado.sql)
amplia a regra de `animais.sexo` para aceitar `Macho`, `Fêmea` e `Não identificado`.
O campo continua obrigatório; registros, RLS e permissões são preservados.
O formulário de cadastro/edição e a validação do catálogo aceitam a nova opção.

Aplicar o arquivo completo no SQL Editor de cada projeto Supabase que recebe
anúncios, antes de usar a nova opção. Não reaplicar a migração inicial.
Esta alteração ainda não foi aplicada nem validada no Supabase remoto.
Validação local: a suíte SQL de remoção, que inclui as regressões das migrações
anteriores, passou com 54 testes. O teste novo confirma INSERT/UPDATE com
`Não identificado` e rejeição de valores inválidos ou nulos.

## Remoção lógica — validada em adotae-testes

A migração [202611120001_remover_anuncio.sql](migrations/202611120001_remover_anuncio.sql)
foi aplicada e validada no Supabase **adotae-testes**, conforme os resultados
informados no encerramento do checkpoint em 13/11/2026:

- Remoção de rascunhos e anúncios publicados validada.
- Remoção de anúncios adotados recusada pela RPC `remover_anuncio`.
- Tentativas de remoção de anúncios de terceiros recusadas com HTTP 403 e código `42501`.
- Fotografias preservadas e novas autorizações de acesso bloqueadas.
- Nenhum problema bloqueante identificado nos testes realizados.

A remoção preenche `removido_em`, preserva os registros e objetos armazenados
e retira o anúncio da listagem e das contagens da interface. O proprietário
mantém leitura técnica para confirmar a operação. Não há exclusão física,
restauração pela interface ou limpeza automática de fotos. URLs assinadas já
emitidas podem funcionar até a expiração; respostas em cache podem continuar
acessíveis.

Foram informados **322 testes Python aprovados** e **140 testes SQL locais
aprovados na última execução registrada**. Os
[testes SQL de remoção](tests/test_remocao_local.py) incluem preservação,
autorização, visibilidade, bloqueio de alterações e concorrência. A validação
remota foi funcional e pontual, não uma reexecução integral da suíte SQL local,
e não certifica o ambiente principal. Nenhuma migração ou operação remota foi
executada nesta atualização documental.

## Marcar anúncio como adotado — validada em adotae-testes

A migração [202611100001_marcar_adotado.sql](migrations/202611100001_marcar_adotado.sql)
requer todas as anteriores. Foi aplicada e validada no Supabase **adotae-testes
em 10/11/2026**, conforme os resultados informados das 12 verificações reais
registradas na seção de diagnóstico de adoção abaixo. A operação foi realizada
pela Data API, com URL e chave publicável de adotae-testes e contas fictícias,
**sem utilização de `service_role`**. Essa validação não certifica o projeto
principal. **A interface Flask ainda não implementa a ação de marcar como adotado.**

A RPC `public.marcar_adotado(p_animal_id uuid)` retorna `void` e permite somente
ao proprietário autenticado, identificado por `auth.uid()`, mudar seu anúncio
`publicado` e `disponivel` para `adotado`. Aceita anúncio já ocultado pela
administração, preservando `oculto_admin`. Altera explicitamente apenas
`status_adocao`; o trigger existente atualiza `atualizado_em`. Não altera
publicação, contatos, fotografias ou objetos, nem exclui o anúncio.
Identidade ausente, parâmetro nulo, anúncio inexistente, alheio, rascunho ou já
adotado geram `42501`. A segunda tentativa é recusada sem novo UPDATE ou mudança
de data. **Não existe operação inversa para usuários comuns.**

Segue o padrão de publicação: entrada pública `SECURITY INVOKER` delega à
função `SECURITY DEFINER` em `adotae_private`, fora dos schemas expostos na API.
Ambas pertencem a `postgres`, usam `search_path` vazio e concedem EXECUTE somente
a `authenticated` além do proprietário da função. EXECUTE é revogado de PUBLIC,
`anon` e `service_role`; chamadas SQL diretas à auxiliar também validam identidade,
propriedade e estado. Não recebe ID de proprietário nem exige nova confirmação
de e-mail. Não usa `service_role` na aplicação. Preserva triggers, RLS, políticas
Storage e grants de tabelas/colunas; aborta se detectar UPDATE efetivo de
`status_adocao` para `authenticated`. O UPDATE direto continua bloqueado.

O bloqueio `FOR NO KEY UPDATE` é adquirido somente no anúncio próprio até o fim
da transação, sem bloquear fotos ou objetos depois dele. Evita inverter a ordem
foto → animal das operações anteriores e é compatível com `KEY SHARE` das FKs.
Em `READ COMMITTED`, duas adoções concorrentes se serializam: a segunda aguarda,
revalida a linha após o COMMIT e recebe `42501`. Se a primeira transação for
desfeita, a segunda pode adotar. Em isolamentos mais fortes, conflitos podem
retornar erro de serialização; transações SQL externas que combinem operações
em ordem inversa continuam responsáveis pelo tratamento de conflitos.

Pelas regras de visibilidade existentes, após a adoção o anúncio deixa de ser
legível publicamente no catálogo e nos detalhes, inclusive por outros usuários
autenticados; continua acessível ao proprietário. As reservas deixam de ser
visíveis para `anon` e novas assinaturas públicas de fotografias são recusadas.
As fotografias e objetos permanecem armazenados, com acesso do proprietário
preservado pelas políticas atuais. URLs assinadas já emitidas **não são revogadas**
pela transição e podem funcionar até a expiração; respostas em cache podem
continuar acessíveis. Não há garantia de revogação imediata nem novo limite de TTL.

Os [testes de adoção](tests/test_adocao_local.py) reutilizam o PostgreSQL
descartável e executam também as regressões de fotografias e visibilidade após
instalar a migração. Comparam grants, RLS e triggers antes/depois, validam os
papéis, campos, data, irreversibilidade, ocultação e duas sessões concorrentes.
Executar a descoberta SQL local e a suíte Python pelos comandos da seção de
publicação abaixo. Auth e Storage continuam sendo stubs locais: esses testes
não validam PostgREST, Storage HTTP, URLs assinadas ou bytes reais e **não
equivalem à validação remota** registrada separadamente neste documento.

Validação local em 10/11/2026, informada anteriormente:

- **87 testes SQL locais** aprovados, incluindo regressões herdadas.
- **247 testes Python** aprovados.

Esses resultados são separados das 12 verificações remotas. As suítes locais
não foram reexecutadas nesta atualização documental. Na validação local
anterior, `git diff --check` não apresentou erros e a revisão independente do
diff não apontou defeitos concretos.

## Assinatura pública individual de fotografias — validada em adotae-testes

A migração [202611080003_fotos_publicas_assinatura.sql](migrations/202611080003_fotos_publicas_assinatura.sql)
requer todas as anteriores. Mantém o bucket `animal-fotos` privado e não altera
grants globais de Storage, funções privilegiadas, interface ou operações do dono.
Foi validada no serviço Storage real do Supabase **adotae-testes em 09/11/2026**,
conforme os resultados informados da execução. **O ambiente principal ainda não
recebeu validação desta funcionalidade.**

Para `anon`, concede somente SELECT de `id`, `animal_id` e `estado` em
`animal_fotos`. `id`/`animal_id` identificam a reserva e compõem o caminho com
`animais.usuario_id`, já legível nas linhas públicas; `estado` é necessário ao
predicado invoker de Storage. Não concede SELECT de tabela, posição ou data.
Na Data API, usar seleção explícita dessas colunas; `select=*` continua recusado.
A RLS permissiva permite somente reservas `pronta` de anúncios `publicado`,
`disponivel`, com `oculto_admin=false`; uma RLS restritiva repete a fronteira para
impedir desvios por políticas permissivas adicionais, inclusive em `animais`.
Os metadados de uma reserva pública podem ser localizados mesmo se o objeto
estiver ausente/divergente; nesse caso a assinatura é recusada. Reservas privadas,
pendentes e em remoção não aparecem para visitantes.

No Storage, a base nova concede SELECT apenas nesse bucket. O guarda restritivo
de SELECT para `anon` aceita exclusivamente `object.sign` e exige os estados
acima, `owner_id=animais.usuario_id::text` e caminho exato
`<usuario_id>/<animal_id>/<foto_id>.webp`. O guarda de papéis admite leitura anon,
mas mantém seu WITH CHECK exclusivo do proprietário autenticado; novos guardas
restritivos barram UPDATE (origem e destino) e DELETE anônimos. Assim, outra
política permissiva não libera escrita, exclusão, listagem, download autenticado,
assinatura em lote, assinatura de upload ou cópia. Os guardas são neutros para
outros buckets, sem lhes conceder acesso. As políticas do proprietário permanecem
iguais. Não há uso de `service_role` no novo fluxo.

Os [testes locais](tests/test_fotos_publicas_local.py) executam consultas e DML
reais sob RLS no PostgreSQL descartável, além das regressões anteriores. O
[roteiro existente](tests/animal_fotos_rls.sql) passa a documentar a exceção por
coluna, preservando a recusa de SELECT total. Executar os comandos locais já
documentados abaixo. O stub usa helpers e metadados simulados: não emite URLs,
não valida PostgREST/HTTP, bytes, cache ou a versão instalada do serviço.

Validação local informada anteriormente: **58 testes SQL locais** e
**232 testes Python** aprovados. Esses resultados são distintos do diagnóstico
remoto abaixo; as suítes locais não foram reexecutadas nesta atualização documental.

Na validação remota em **adotae-testes em 09/11/2026**, foram informados os
seguintes resultados:

- Cliente anônimo, sem sessão autenticada, consultou uma reserva `pronta` de
  anúncio público, apenas com as colunas autorizadas.
- A URL assinada de **60 segundos** foi gerada com sucesso; seu download retornou
  **HTTP 200** e WebP válido.
- O download direto anônimo foi bloqueado. As listagens anônimas do bucket e dos
  prefixos testados não expuseram arquivos.
- A assinatura de fotografia `pronta` pertencente a rascunho foi recusada, e a
  reserva privada não ficou visível para visitante.
- `select=*` foi recusado com `42501`.
- O download autenticado pelo proprietário foi preservado.

Esse diagnóstico remoto foi funcional e pontual, não uma reexecução dos
58 testes SQL locais ou dos 232 testes Python. Cenários remotos de anúncios
adotados, ocultos e fotografias em outros estados **não foram confirmados neste
diagnóstico**.

Antes de aplicar em outro ambiente remoto, revisar o diff e confirmar que o bucket
continua privado e que o servidor oferece os
[helpers de operação](https://supabase.com/docs/guides/storage/schema/helper-functions).
Para ampliar a validação em ambiente de testes autorizado, verificar pela API,
com chave pública/anon e
sessões de dois proprietários: assinatura individual pública e acesso à URL;
recusa para cada estado privado e vínculo inválido; listagem, download direto,
assinatura em lote/upload, escrita, copy/move; upload/download do dono e isolamento
do outro usuário; seleção explícita na Data API sem reservas privadas e recusa
de `select=*`. Preparar arquivos exclusivamente pela API Storage, nunca por DML
em `storage.objects`. Verificar os identificadores de operação efetivamente
usados pelo servidor conforme o [controle de acesso](https://supabase.com/docs/guides/storage/security/access-control).
`storage.operation` é contexto confiável do serviço, não prova de origem para
clientes com acesso SQL arbitrário.

Na integração futura, o Flask poderá solicitar URLs com `expiresIn=60`
(60 segundos). Esse prazo será uma escolha da aplicação: `expiresIn` é definido
por quem solicita a URL, e a RLS atual não estabelece um prazo máximo. Visitantes
podem chamar a API diretamente e solicitar outros prazos aceitos pelo Storage.
Ocultar um anúncio impede novas assinaturas, mas não revoga URLs existentes,
que podem continuar utilizáveis até sua expiração. Cache também pode prolongar
o acesso a respostas anteriores. Essa limitação é aceita conscientemente para
o MVP acadêmico, sem garantia de revogação imediata.

## Publicação direta — validada em adotae-testes

A migração [202611080002_publicar_anuncio.sql](migrations/202611080002_publicar_anuncio.sql)
acrescenta `public.publicar_anuncio(p_animal_id uuid)`, sem botão ou rota Flask.
Foi aplicada com sucesso no Supabase **adotae-testes em 08/11/2026**,
com as verificações de instalação e permissões da RPC aprovadas, conforme os
resultados informados. Em novas instalações, executar somente após as migrações
anteriores e revisão. O bucket `animal-fotos` permanece privado e suas políticas/grants não mudam.

A entrada pública é `SECURITY INVOKER` e delega à auxiliar `SECURITY DEFINER`
em `adotae_private`, fora dos schemas expostos. Ambas pertencem a `postgres`,
têm `search_path` vazio e EXECUTE concedido explicitamente a `authenticated`,
revogado de PUBLIC, `anon` e `service_role`. A auxiliar também valida chamadas
SQL diretas: identidade por `auth.uid()`, e-mail confirmado em
`auth.users.email_confirmed_at`, propriedade, estado `rascunho`, disponibilidade,
ausência de ocultação, contato e foto `pronta`. Os CHECKs existentes validam o
formato dos contatos. Exige objeto em `animal-fotos` com `owner_id` correspondente
e caminho exato `<usuario_id>/<animal_id>/<foto_id>.webp`.

A única alteração explícita no animal é `status_publicacao='publicado'`; o
trigger existente atualiza a data e mantém a proteção dos anúncios publicados.
Não há UPDATE direto desse campo para usuários comuns; a migração aborta se
detectar privilégios efetivos divergentes. Retorna `void` em sucesso. Ausência,
falta de autorização ou condição não atendida geram `42501`, sem distinguir
anúncio alheio de inexistente. Repetir a chamada após sucesso também gera `42501`
sem alterar a linha: a transição aceita exclusivamente rascunhos. Uma disputa de
locks retorna `55P03` sem publicar; uma nova chamada após encerrar a disputa
revalida todas as condições. Não há repetição automática dentro da função.

Bloqueios seguem **auth.users → fotos → animal → objeto**: todas as reservas existentes são
bloqueadas por ID em ordem crescente, depois o animal com `NO KEY UPDATE` e o
objeto comprovante com `SHARE`, até o fim da transação. Apenas os IDs efetivamente
bloqueados podem comprovar a publicação. Reservas criadas durante a operação podem
exigir nova chamada. Todos os locks da publicação usam `NOWAIT`, inclusive o da
conta Auth, para abortar imediatamente em disputa. Isso impede ciclos com UPDATEs
de remoção em lote, cuja ordem de visita das fotos não é controlada pela RPC.
A confirmação é redefinida na nova migração para bloquear
explicitamente foto antes de animal, revalidando propriedade e estado após esperar,
sem alterar suas demais regras. O novo trigger invoker de fotografias bloqueia o
animal com `SHARE` antes de permitir UPDATE em rascunhos. Assim, remoção primeiro
faz a publicação concorrente recusar o lock e uma nova chamada reavaliar a foto;
publicação primeiro faz a remoção esperar e falhar após o COMMIT da publicação.
Fotos pendentes de anúncios publicados também não podem ir para `removendo`.
Operações confiáveis de `postgres`/`service_role` preservam seus controles anteriores.

Os [testes de publicação](tests/test_publicacao_local.py) executam SQL real em
PostgreSQL local descartável, reutilizando a infraestrutura e regressões das
fotografias. Cobrem autorizações, pré-condições, vínculo do objeto, grants,
repetição, proteção de fotos e concorrência com confirmação, remoção, outra
publicação, nova reserva após conflito e remoção com duas fotos em ordem inversa.
O teste desta última reproduziu `40P01` antes da correção com `NOWAIT`.
O roteiro de Storage foi adaptado somente
para exigir bloqueio da remoção em publicado e usar fixture privilegiada em
rascunho nos cenários posteriores de remoção.

Validação local em 08/11/2026, conforme os resultados informados:
**39 testes SQL de integração** aprovados no PostgreSQL 18
(incluindo regressões herdadas), **180 testes Python** aprovados e
`git diff --check` sem erros.

Na validação remota em **adotae-testes em 08/11/2026**, conforme os resultados
informados, um usuário autenticado publicou seu próprio rascunho pela RPC
`publicar_anuncio`. Uma consulta confirmou o status `publicado`; a fotografia
permaneceu `pronta`, com o objeto presente no Storage. Uma tentativa de alterar
o estado da fotografia para `removendo`, usando a sessão do proprietário,
foi recusada com `42501`. Uma nova consulta confirmou que a fotografia
permaneceu `pronta`.

Esses testes remotos foram **funcionais e pontuais**, não uma reexecução integral
da suíte SQL local.

Com o servidor descartável local já iniciado conforme a seção de testes abaixo:

```powershell
$env:ADOTAE_TEST_LOCAL_PG = '1'
.\.venv\Scripts\python.exe -m unittest discover -s supabase/tests -p 'test_*local.py' -v
Remove-Item Env:ADOTAE_TEST_LOCAL_PG
.\.venv\Scripts\python.exe -m unittest discover -v
git diff --check
```

Limites: Auth e Storage são simulados no banco local; não certificam o serviço
Supabase implantado, a API ou a existência dos bytes físicos. O SQL confere o
catálogo do Storage e o estado da reserva, sem decodificar novamente a imagem.
A preservação da foto vale para os usuários comuns; operadores privilegiados
continuam responsáveis por suas intervenções. As operações isoladas seguem a
mesma ordem de locks; transações SQL externas que combinem operações em ordem
inversa ainda precisam tratar deadlock/serialização. Nenhuma integração do
catálogo ou leitura pública de fotos foi implementada nesta etapa.

## Proteção da edição de anúncios publicados — validada em adotae-testes

A migração [202611080001_protect_animais_edicao.sql](migrations/202611080001_protect_animais_edicao.sql)
foi aplicada com sucesso no Supabase **adotae-testes em 08/11/2026**.
As verificações de privilégios e de ativação do trigger foram aprovadas.
**Ainda não foi aplicada no projeto Supabase principal.**
Mantém a RLS de propriedade e os campos editáveis dos rascunhos, revoga UPDATE
de `status_adocao` para `authenticated` e aborta se grants de tabela, PUBLIC ou
papéis herdados ainda permitirem UPDATE efetivo em campos protegidos.

O trigger `BEFORE UPDATE`, `SECURITY INVOKER` com `search_path` vazio, bloqueia
mudanças de identificação, proprietário, estados, moderação e criação por usuários
comuns. Em publicados, compara a linha inteira com `IS DISTINCT FROM`, exceto
`email_contato`, `whatsapp` e `atualizado_em`. O trigger `animais_atualizar_data`
executa antes, por ordem alfabética, e continua responsável pela data automática.
Os CHECKs existentes validam contatos e impedem remover o último de um publicado.
Atualizações que repetem valores existentes continuam permitidas nas colunas
com grant. `postgres` e `service_role` preservam operações privilegiadas conforme
os grants/RLS existentes; a decisão usa o papel SQL, não claims JWT. Não há
novas permissões, RPCs, integração Flask ou disponibilização de `service_role`.

O [teste SQL de edição](tests/animais_edicao.sql) roda pelo mecanismo local abaixo,
com Auth/Storage simulados e ROLLBACK. Exercita rascunhos, contatos, campos
protegidos, NULLs, isolamento, data automática e operações privilegiadas. A suíte
também verifica rejeição de privilégios efetivos amplos (tabela, PUBLIC e papel
herdado), preservação dos metadados/permissões/políticas das fotos e os cenários
anteriores de confirmação, remoção e concorrência. O roteiro manual de animais
foi ajustado: adoção é fixture privilegiada, e publicados ocultos editam contatos.
Os relatos históricos de validação remota das etapas anteriores abaixo não
substituem a validação específica desta migração registrada nesta seção.

Validação local anterior: **13 testes SQL de integração** no PostgreSQL 18
e **162 testes Python** passaram. O teste de bloqueio de nome publicado falhou
antes da nova migração, confirmando a lacuna.

O teste remoto em **adotae-testes** foi aprovado nas quatro verificações:
edição de rascunho, edição de contato publicado, bloqueio da alteração da
descrição publicada e exigência de contato na publicação. Foi executado com
`ROLLBACK`, sem persistir registros fictícios. Esses quatro cenários não
constituem validação exaustiva de todas as políticas.

## Primeira fotografia em `/anunciar`

O Flask processa uma foto opcional antes de criar o rascunho, reserva a posição
1, envia WebP sem upsert e chama `public.confirmar_foto` com o cliente autenticado.
Confere `pronta` pela Data API antes de informar conclusão. Os testes dessa
integração são offline; não alteram as validações remotas registradas abaixo.

Falhas parciais preservam o rascunho e exibem aviso específico. Não há compensação
automática, mudança para `removendo`, exclusão ou novo upload. Uma falha na reserva
pode ter ocorrido após o INSERT remoto; uma falha de upload pode deixar bytes
armazenados; uma falha de confirmação/leitura pode deixar `pendente` ou uma foto
já `pronta`. Interrupções do processo também podem deixar operações incompletas,
sem que a aplicação consiga entregar o aviso. A associação usuário/animal/reserva
permite identificar o caminho posteriormente, mas não há recuperação pela interface.

A menor evolução para retomar o fluxo é consultar a reserva existente do próprio
rascunho e repetir a RPC idempotente, sem reenviar cegamente o arquivo. Cancelar
e liberar a posição requer uma operação controlada adicional: as permissões
atuais não permitem DELETE de reservas, e `removendo` é terminal. Essa operação
precisa tratar uploads em andamento e confirmar remoção pela API Storage antes
de limpar a reserva; não basta ampliar grants ou excluir `storage.objects` por SQL.
Nenhuma migração ou política foi modificada nesta integração.

## Validação do Storage e limitação de cache

O Storage utiliza bucket privado e políticas de acesso. A validação real informada
em **adotae-testes** confirmou reserva, upload, confirmação por RPC (incluindo
repetição), download pelo proprietário da foto `pronta` e bloqueio de outros
usuários e operações proibidas. Após a mudança para `removendo`, novas requisições
com `cacheNonce` novo foram bloqueadas. A URL previamente utilizada ainda entregou
bytes, comportamento compatível com cache da CDN; os cabeçalhos não comprovaram
a origem exata do acesso residual nem uma falha nas políticas RLS.
O ambiente foi limpo: zero animais, fotografias e arquivos restantes.

A [documentação oficial sobre arquivos privados após revogação](https://supabase.com/kb/troubleshooting/storage-private-bucket-files-still-accessible-after-remove-access-or-logout)
explica que a CDN pode entregar respostas previamente autorizadas sem consultar
novamente o Storage. O `cache-control: 0` do upload não comprova ausência desse
cache. O acesso residual continua sendo um risco de confidencialidade, mesmo
quando uma nova consulta ao serviço é bloqueada. Respostas previamente armazenadas
em cache podem permanecer acessíveis temporariamente: a revogação imediata desse
conteúdo não é garantida. Essa limitação foi aceita para o escopo acadêmico do Adotaê.

Inspeção **local** de `storage3 2.33.0`: `download(path, options=None,
query_params=None)` faz um GET em `/storage/v1/object/<bucket>/<caminho>`, sem
HEAD ou consulta de informações, repassando os parâmetros de query a `_request`.
Este preserva os cabeçalhos da sessão, usa o transporte HTTPX e converte erros
HTTP em `StorageApiError`; não há cache próprio nesse caminho. `download()`
retorna somente bytes, sem expor cabeçalhos como `Age` ou `CF-Cache-Status`.

Alteração mínima no [roteiro](tests/validar_storage.py):

- As verificações de leitura usam `query_params={"cacheNonce": uuid4().hex}`,
  com valor novo por chamada, mantendo rota, sessão, bucket e caminho.
  É o [bypass documentado da CDN](https://supabase.com/docs/guides/storage/cdn/smart-cdn#bypassing-cache).
  Não basta acrescentar uma query arbitrária, que a Smart CDN pode desconsiderar.
- Em `pronta`, compara os bytes tanto com nonce quanto na URL habitual, também
  aquecendo esta última para observar o risco de revogação.
- Em `removendo` e após exclusão, exige bloqueio com nonce nos checkpoints
  `download_removendo_sem_cache` e `download_excluido_sem_cache`, e depois verifica
  a URL habitual. Se somente esta entregar bytes, registra aviso de possível
  acesso residual por cache, sem classificá-lo como falha RLS nem comprovar
  revogação imediata. As verificações restantes e a exclusão continuam normalmente.
- O resultado é **APROVADO** sem acesso residual, ou **APROVADO COM RESSALVA**
  quando todas as verificações obrigatórias e o encerramento das sessões passam
  e resta somente o acesso residual conhecido. Qualquer outro erro ou violação,
  inclusive sucesso com nonce após revogação, mantém a reprovação.
- Mantém recusas de operações proibidas, isolamento A/B/visitante, comparação
  dos bytes, manifesto e limpeza por caminho exato no `finally`. Não amplia SQL,
  permissões, credenciais, dependências ou configuração do bucket.

Limites: os testes offline verificam o contrato HTTP do SDK e simulam cache;
não reproduzem a CDN nem certificam a versão implantada. O exemplo oficial de
nonce usa URL assinada; o roteiro usa a rota autenticada do SDK, sem gerar URL
assinada. O nonce solicita uma consulta nova, mas é necessário correlacionar os
checkpoints com logs do Storage na implantação para confirmar que o bypass
chegou ao serviço. Um 200 com nonce continua sendo falha a investigar; não prova
sozinho que a RLS autorizou. Um 404 aceito como objeto invisível continua sem
comprovar ausência física. Não há fallback para outra rota, espera arbitrária,
purge, alteração de JWT ou repetição até obter o resultado desejado.

Em eventual execução separadamente autorizada,
correlacionar os novos checkpoints com GETs e horários nos logs seguros, sem
registrar JWTs, cabeçalhos de autenticação, respostas ou URLs completas. Se o
bypass for bloqueado e a URL habitual entregar bytes, há evidência compatível
com cache, sujeita à ressalva descrita acima. Os testes offline não comprovam
revogação imediata na CDN.

## Fotografias — SQL aplicado em adotae-testes; histórico da investigação

Conforme o resultado informado, a [migração de fotografias](migrations/202611060001_create_animal_fotos.sql)
foi aplicada em **adotae-testes**, e a versão anterior do [roteiro SQL de segurança](tests/animal_fotos_rls.sql)
passou sem erros, com tabelas vazias após `ROLLBACK`. O bucket privado `animal-fotos`
foi criado e conferido: `public=false`, `file_size_limit=512000` e
`allowed_mime_types=['image/webp']`. Os comentários históricos nos arquivos SQL
não representam esse novo resultado; as migrações aplicadas não foram modificadas.
Na execução real posteriormente informada, upload, primeira e segunda confirmação
da RPC e conferência da reserva passaram. `download_proprio` falhou com
`StorageApiError`, HTTP 404, código `not_found`. A limpeza foi concluída e
conferida: **0 animais, 0 reservas e 0 arquivos no bucket**. O fluxo completo de
Storage ainda não havia sido aprovado naquela execução. Não há processamento,
integração Flask ou acesso público a fotografias. Não extrapolar esses resultados
para o projeto principal.

### Hipótese do download e migração corretiva proposta

A migração original aplicada autoriza somente `object.get_authenticated` para
fotos próprias `pronta`. A [documentação oficial de controle de acesso](https://supabase.com/docs/guides/storage/security/access-control)
usa `object.get_authenticated` e `object.get_authenticated_info` no exemplo de
leitura sem listagem. A ausência da segunda operação **pode explicar** uma
negação caso a versão/rota efetivamente utilizada a propague; não é causa
comprovada do 404 observado. Os testes SQL anteriores também a negavam.

Há evidência que limita essa hipótese: o SDK Python instalado faz um único
`GET /object/<bucket>/<caminho>` em `download()`, sem consulta prévia de informações.
No [código oficial atual da rota de download](https://github.com/supabase/storage/blob/master/src/http/routes/object/getObject.ts),
essa rota usa `GET_AUTH_OBJECT` (`object.get_authenticated`), já permitido.
Já a [rota de informações](https://github.com/supabase/storage/blob/master/src/http/routes/object/getObjectInfo.ts)
usa `GET_AUTH_OBJECT_INFO` (`object.get_authenticated_info`); HEAD tem operação
distinta e continua bloqueado. O código upstream não certifica a versão implantada
em `adotae-testes`. O 404 isolado não distingue objeto ausente de objeto invisível
por RLS, nem confirma JWT, caminho ou políticas efetivamente usados no download.
O sucesso da RPC confirma suas verificações naquele instante; não prova leitura
pela sessão, pois a consulta ao catálogo ocorre na auxiliar `SECURITY DEFINER`.

A nova [202611070001_fix_animal_fotos_storage_leitura.sql](migrations/202611070001_fix_animal_fotos_storage_leitura.sql)
é uma proposta de compatibilidade com a leitura documentada, **não aplicada**.
Usa `ALTER POLICY` exclusivamente no `USING` de `animal_fotos_storage_leitura`:
troca o filtro de operação de `pronta` por `allow_any_operation` com as duas
operações. Preserva `RESTRICTIVE`, SELECT, papel `authenticated`, proprietário
atual do animal, caminho exato, neutralidade para outros buckets e SELECT auxiliar
de `object.delete`/`object.delete_many` somente em `removendo`. Não altera a
migração original, grants, RPC, política de exclusão ou configuração do bucket
privado. O único acesso novo é a consulta autenticada de informações da própria
foto `pronta`; listagem, URLs assinadas e demais rotas continuam bloqueadas.

Verificações desta proposta e próximas etapas:

1. Localmente, aplicar as três migrações em ordem no PostgreSQL descartável com
   Auth/Storage simulados e executar o [roteiro SQL](tests/animal_fotos_rls.sql).
   Ele autoriza ambas as leituras para própria `pronta`, nega `pendente`,
   `removendo`, usuário B e caminhos forjados, e mantém as negações de assinatura,
   listagem e operações alternativas. Os testes preservam as verificações de
   exclusão, visitante, grants e políticas de animais. A integração local também
   compara todas as políticas de Storage antes/depois, exceto o `USING` ajustado.
2. Somente em etapa futura autorizada, revisar/aplicar **apenas a nova migração**
   em `adotae-testes`, conferir bucket privado, grants e políticas efetivas e
   repetir o roteiro Python com sessões A/B e arquivo descartável. Conferir bytes
   do download, isolamento, operações proibidas e exclusão. Validar separadamente
   a rota de informações própria/alheia e estados; o roteiro Python não a chama.
3. Correlacionar a requisição com logs seguros do Storage para identificar a
   operação propagada e a versão instalada. Se continuar em 404, conferir sessão
   autenticada, bucket/caminho exatos, estado/propriedade e combinação de políticas;
   não ampliar permissões por tentativa. Não registrar tokens, senhas ou URLs
   assinadas. Confirmar novamente a limpeza de animais, reservas e arquivos.

Nesta revisão não houve SQL no Supabase nem acesso remoto ao Storage. O roteiro
`validar_storage.py` permanece intacto: seus checkpoints e comparação dos bytes
já permitem repetir o cenário sem enfraquecer os critérios de sucesso. Testes
locais não reproduzem o HTTP 404 real nem demonstram que a proposta o elimina.

**Resultados locais desta revisão:** o teste ampliado falhou contra a migração
original em `object.get_authenticated_info`; após aplicar a corretiva no banco
simulado, os **11 testes SQL locais** passaram no PostgreSQL 18, incluindo o
roteiro até `ROLLBACK` e a concorrência. A suíte Python passou com **110 testes**.
`git diff --check` e a conferência de whitespace dos arquivos novos passaram.
Esses resultados não substituem a validação futura no serviço real.

### Modelo e autorização

`public.animal_fotos` usa UUID e data gerados pelo banco, FK obrigatória para
`animais` com `NO ACTION`, posição `smallint` entre 1 e 3 e estados textuais com
`CHECK`. `UNIQUE(animal_id, posicao)` impede reservas duplicadas inclusive em
transações concorrentes; reservas pendentes ou em remoção também ocupam posição.
O índice único atende consultas por animal e a FK. Não há coluna de URL ou caminho:
o caminho canônico será `<usuario_id>/<animal_id>/<foto_id>.webp`.

Somente `authenticated` recebe SELECT, INSERT nas colunas `animal_id,posicao`
e UPDATE em `estado`. Visitantes não recebem privilégios. RLS verifica o dono
explicitamente, mesmo quando o animal de outro usuário é legível publicamente.
Reservas exigem animal próprio em rascunho; nenhuma política de `animais` é alterada.

| Operação | Cliente proprietário | Operador confiável (`postgres`/`service_role`) |
| --- | --- | --- |
| Inserir reserva | Somente `pendente`, valores padrão; animal em rascunho | Também inicia `pendente` |
| `pendente → pronta` | Somente RPC de confirmação, após verificar reserva/objeto | Permitido pelo trigger |
| `pendente → removendo` | Permitido | Permitido |
| `pronta → removendo` | Permitido | Permitido |
| Retornar de `removendo`, outras transições ou repetir estado | Proibido | Proibido pelo trigger |
| Alterar ID, animal, posição ou data | Proibido | Proibido pelo trigger |
| Excluir reserva | Sem grant/política | Limpeza futura após remoção confirmada pela API Storage |

RLS não compara OLD e NEW e não comprova processamento de imagens. A menor
solução adotada combina grants por coluna, RLS e um trigger `SECURITY INVOKER`
com `search_path` vazio. UPDATE direto para `pronta` continua proibido para
clientes; somente a auxiliar privada de confirmação eleva privilégios. A função do trigger não é
executável por clientes. Papéis confiáveis continuam sujeitos ao trigger, mas
um administrador capaz de desativá-lo está fora desse limite de proteção.

O custo desta etapa conservadora é não liberar posições automaticamente:
`removendo` é terminal para clientes. Não há limpeza, expiração ou recuperação
automática de reservas ainda. Não ampliar grants para contornar
isso. A futura etapa de processamento precisará coordenar SQL e Storage: upload,
cancelamento e exclusão não formam uma transação única. Deve tratar uploads em
andamento, falhas e tentativas repetidas antes de apagar reservas ou liberar vagas.
Não colocar `service_role` no navegador nem acrescentá-lo ao Flask nesta etapa.

### Confirmação com a sessão autenticada

`public.confirmar_foto(p_foto_id uuid)` retorna `void`, usa `SECURITY INVOKER` e
apenas delega para `adotae_private.confirmar_foto(uuid)`. A auxiliar usa
`SECURITY DEFINER`, pertence explicitamente a `postgres` e executa o UPDATE aceito
pelo trigger. Ambas fixam `search_path = ''`, usam nomes qualificados e recebem
EXECUTE somente para `authenticated`, além dos direitos do proprietário. Grants
padrão de `PUBLIC`, `anon` e `service_role` são removidos das duas funções.

O schema privado concede apenas USAGE para `authenticated`, sem CREATE. O
wrapper INVOKER necessita desse USAGE e do EXECUTE da auxiliar; por isso uma
conexão SQL autenticada também consegue chamar a auxiliar diretamente, sujeita
às mesmas verificações. O isolamento da Data API depende de manter
**`adotae_private` fora de Exposed schemas**. A migração e o roteiro recusam a
configuração `pgrst.db_schemas` que mencione o schema, quando essa configuração
estiver disponível na conexão. Isso não certifica a configuração efetiva do
serviço; conferi-la antes da aplicação e testar que a auxiliar não tem endpoint.
Nenhum comando altera Exposed schemas ou configura a Data API.

A auxiliar exige `auth.uid()`, filtra propriedade antes de bloquear a reserva
com `FOR UPDATE` e mantém um lock `FOR SHARE` no animal para preservar o
rascunho durante a confirmação. Recusa reserva inexistente, alheia, em remoção
ou de animal publicado. Consulta somente o objeto no bucket `animal-fotos`,
no caminho derivado de usuário/animal/foto e com `owner_id` igual ao usuário.
Não recebe proprietário, caminho ou estado como parâmetro; não modifica animais,
IDs, posições, datas ou metadados do Storage. Não concede SELECT adicional ao
chamador nem altera `storage.operation` para contornar políticas.

Se `metadata.size` estiver disponível, precisa representar de 1 a 512000 bytes
inteiros; se `metadata.mimetype` estiver disponível, precisa ser `image/webp`.
Campos ausentes ou JSON null são tratados como indisponíveis, conforme a regra
desta etapa. A configuração do bucket continua obrigatória e independente.
`pronta` significa **objeto registrado e associado corretamente**, sem afirmar
decodificação, processamento ou validade dos bytes. A RPC pode ser chamada
diretamente pelo usuário, independentemente de uma futura validação no Flask.

Uma repetição em `pronta` revalida proprietário, rascunho, objeto e metadados,
mas retorna sem UPDATE. Se o upload terminar e a resposta se perder, pode-se
repetir a confirmação; um conflito no upload não substitui essa verificação.
Confirmação e solicitação de remoção concorrem pelo lock da foto: se a confirmação
termina primeiro, a remoção pode seguir; se a remoção termina primeiro, a
confirmação falha, sem ressuscitar a reserva. Isso não torna SQL e armazenamento
de bytes uma única transação. Falhas na remoção mantêm `removendo` para nova
tentativa via API; liberar posições continua dependendo de solução futura.

O SDK Python já instalado permite a chamada futura com o cliente da sessão:
`g.auth_cliente.rpc('confirmar_foto', {'p_foto_id': foto_id}).execute()`.
Nenhuma alteração Flask ou credencial administrativa foi adicionada.
Referências: [segurança e privilégios de funções](https://supabase.com/docs/guides/database/functions),
[exposição de schemas](https://supabase.com/docs/guides/api/using-custom-schemas).

### Políticas de Storage com a correção proposta

As seis políticas são limitadas ao bucket `animal-fotos`: uma base permissiva,
um guarda restritivo de papéis e quatro guardas restritivos por comando. Os
guardas se somam por AND às políticas permissivas existentes, evitando que uma
política ampla de outro recurso libere este bucket. Para outros buckets são
neutros; os GRANTs globais e políticas existentes do Storage não são alterados.

| Operação Storage | Condição adicional ao proprietário e ao caminho exato |
| --- | --- |
| `object.upload` | Reserva `pendente`, animal ainda em rascunho; somente INSERT |
| `object.get_authenticated`, `object.get_authenticated_info` | Reserva própria `pronta`; download e informações autenticados (segunda operação requer a migração corretiva) |
| `object.delete`, `object.delete_many` | Reserva `removendo`; SELECT e DELETE somente nessas operações |
| UPDATE, upsert, copy/move, assinatura de URL de leitura/upload, listagem | Bloqueados |
| S3, TUS, transformações e outras rotas | Sem autorização nesta etapa |

O caminho é comparado integralmente com UUIDs canônicos obtidos das tabelas,
sem converter segmentos enviados pelo cliente em UUID. Isso recusa IDs forjados,
extensão diferente, diretórios extras e prefixos de outro usuário. A autorização
vem da reserva e do proprietário atual do animal, sem depender de `owner_id`
ou metadados enviados no upload.

Um SELECT genérico também permitiria outras ações. Por isso os filtros usam
`storage.allow_only_operation()` e `storage.allow_any_operation()`, com nomes
exatos das operações. Sem contexto de operação, esses helpers negam acesso.
A migração falha se faltarem os helpers ou RLS em `storage.objects`; não instala
substitutos. Referências oficiais verificadas para esta proposta:
[helpers e normalização](https://supabase.com/docs/guides/storage/schema/helper-functions),
[controle de acesso e upsert](https://supabase.com/docs/guides/storage/security/access-control),
[nomes das operações](https://github.com/supabase/storage/blob/master/src/http/routes/operations.ts).

Em futuras instalações, criar após autorização **somente** o bucket `animal-fotos`, com `public: false`,
`file_size_limit: 512000` (500 KiB) e `allowed_mime_types: ['image/webp']`.
Conferir os três valores antes de enviar arquivos. O limite global precisa
permitir esse tamanho. [Criação de buckets](https://supabase.com/docs/guides/storage/buckets/creating-buckets).
Não habilitar exposição de tabela na Data API nem editar diretamente os
metadados de `storage.objects`. MIME e extensão não comprovam conteúdo WebP
válido; validação/decodificação ficará para a etapa de processamento.

### Validação SQL e limites

O roteiro usa dois usuários Auth existentes, claims simulados e papéis locais,
como o roteiro de animais. Funções temporárias verificam resultados e SQLSTATEs
automaticamente, recuperando erros esperados em subtransações. Qualquer erro não
esperado deve interromper a execução e ser seguido de `ROLLBACK`. O roteiro
sempre termina em `ROLLBACK`; não cria usuários nem escreve em `storage.objects`.
Executar com o operador `postgres`, não com um cliente. A/B devem ser distintos.

São verificáveis em SQL: privilégios efetivos de tabela/coluna (incluindo herança),
RLS, propriedade A/B, visitantes e ausência de identidade, defaults, posições,
unicidade, FK sem cascata, transições e campos imutáveis. O roteiro simula grants
e uma política UPDATE indevidos dentro de SAVEPOINT para conferir a defesa do
trigger. Isso não torna seguro ampliar grants: INSERT amplo, por exemplo, permite
escolher ID/data. A auditoria inicial deve passar integralmente antes de usar a API.
Repetir a auditoria depois de qualquer mudança de exposição da Data API.

Os testes de Storage em SQL avaliam os predicados realmente instalados sobre
caminhos sintéticos e verificam os guardas restritivos; uma política ampla
temporária também testa o bloqueio anônimo e a neutralidade para outros buckets.
Não testam bytes, a interpretação HTTP ou a composição em operações reais.
O final do roteiro descreve o teste de unicidade em duas conexões concorrentes.
Também audita os privilégios efetivos das funções/schemas, modo de execução,
proprietário e search_path, e testa RPC sem objeto, sem identidade, como visitante,
como outro usuário e em estados proibidos. Seu bloco comentado de confirmação
válida exige uma reserva/arquivo previamente preparados pela API autorizada;
**não foi executado com Storage real**. Não criar essas fixtures por SQL no serviço.
Para comparar `animais` antes/depois de aplicar a migração, guardar suas ACLs e
`pg_policies` previamente; o snapshot interno do roteiro verifica apenas que
o próprio teste não altera essas definições.

O [teste local de integração SQL](tests/test_animal_fotos_local.py) usa somente
Python padrão e o `psql` instalado. Requer ativação explícita, servidor temporário
em `127.0.0.1:55439` e `data_directory` chamado `adotae-fotos-pg-*`; cria e remove
um banco aleatório apenas nessa instância. Descarta variáveis PG herdadas que
poderiam redirecionar a conexão. Não inicia servidores nem cria recursos remotos.
Exemplo, com a instância temporária local já iniciada:

```powershell
$env:ADOTAE_TEST_LOCAL_PG = '1'
.\.venv\Scripts\python.exe supabase/tests/test_animal_fotos_local.py
Remove-Item Env:ADOTAE_TEST_LOCAL_PG
```

Nesse banco, Auth e Storage são simulados. **Somente o stub local** recebe
fixtures de objetos por SQL; não há bytes, bucket ou API Storage reais. Os testes
executam as quatro migrações e os roteiros automatizados completos, confirmam sucesso/isolamento, caminhos,
bucket e proprietário divergentes, tamanho/MIME presentes ou ausentes, estado
em remoção e repetição sem UPDATE (campos e `ctid` preservados). A concorrência
usa dois processos `psql` e observa espera real por lock, nas duas ordens entre
confirmação e remoção. A comparação antes/depois da migração confere políticas
e ACLs de animais e ACLs de Storage intactas.

**Resultados locais desta etapa:** os 10 testes SQL locais, incluindo o roteiro
até `ROLLBACK` e a concorrência, passaram no PostgreSQL 18. A suíte Python
existente também passou separadamente com **87 testes**. A revisão independente
não encontrou falhas críticas/importantes. O lock `FOR SHARE` do animal não foi
testado contra publicação concorrente; a concorrência automatizada desta etapa
cobre confirmação/remoção da foto. Na etapa anterior, também foram verificados
COMMIT/ROLLBACK concorrentes de reservas e limite de três posições. Nenhum
resultado local valida a configuração ou versão de um projeto Supabase;
nenhum teste remoto foi feito.

O checklist mais amplo abaixo permanece para etapas futuras. O roteiro Python
ao final deste documento cobre somente os cenários solicitados nesta etapa;
não executa automaticamente todo este checklist.
Após revisão e autorização, ainda será necessário validar no `adotae-testes`,
com bucket privado e sessões reais A/B (sem chave privilegiada nas chamadas):

1. Reservar como A e enviar WebP para o caminho exato com `upsert: false`.
   Recusar visitante/B, animal B, reserva inexistente, estado incorreto,
   animal publicado, diretórios extras e extensões diferentes.
2. Repetir upload no mesmo caminho e tentar `upsert: true`, UPDATE, copy/move
   dentro do bucket e entre buckets, inclusive origem/destino com reservas
   válidas. Todos devem falhar sem alterar bytes ou criar novos objetos.
3. Download de `pronta` apenas pelo dono. Recusar `pendente`/`removendo`, B,
   visitante, URL pública e listagem. Tentar criar URLs assinadas individuais,
   em lote e de upload: nenhuma URL/token utilizável deve ser emitido, nem
   quando se usar o caminho correto de uma reserva do próprio usuário.
4. Solicitar `removendo` e remover via API como dono. Recusar remoção antes
   disso e por B. Conferir bytes/objetos depois: respostas vazias ou HTTP de
   sucesso podem mascarar uma exclusão negada. Reserva permanece até limpeza
   confiável futura; não usar DELETE SQL em `storage.objects`.
5. Conferir o limite de 512000 bytes (aceito) e 512001 (recusado), MIME WebP
   e outros MIME recusados. Para testar os limites, usar novas reservas próprias.
6. Conferir os nomes de operação propagados pela versão real do serviço,
   inclusive SELECT auxiliar da remoção, a composição com políticas existentes
   e a negação das rotas alternativas. Usar arquivos descartáveis; a limpeza
   de bytes depende da API e não é desfeita por ROLLBACK SQL.
7. Confirmar via RPC com a sessão de A, repetir e conferir campos preservados;
   negar B/visitante, reserva sem objeto ou em remoção. Conferir metadados reais
   e a propagação do JWT. Verificar que somente o wrapper INVOKER está exposto,
   sem endpoint para `adotae_private`, e repetir a auditoria de grants efetivos.
   Testar confirmação versus remoção concorrentes no serviço real.

As seções seguintes registram o histórico da etapa de **animais**; aprovações
remotas e contagens anteriores não se aplicam à validação real do Storage.

O arquivo [202611010001_create_animais.sql](migrations/202611010001_create_animais.sql)
cria `public.animais` no Supabase PostgreSQL. A migração foi aplicada em
`adotae-testes` e no projeto principal. O catálogo agora consulta os anúncios
públicos com cliente anônimo e filtros explícitos, sem contatos, proprietário
ou fotografias. A integração Flask foi validada offline com SDK real e HTTP
simulado; não houve nova migração, mudança de RLS ou validação remota nesta etapa.
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
  em `run.py`; a migração inicial admite `Macho` e `Fêmea` em `sexo`.
  A migração de 13/11 acrescenta `Não identificado`.
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
de outra pessoa é legível publicamente. Após a migração de proteção de edição,
o proprietário pode editar os dados permitidos dos rascunhos; nos publicados,
inclusive ocultos/adotados, somente os contatos. O grant de DELETE é preservado,
sujeito às FKs existentes. O proprietário não pode remover a ocultação.

`anon` recebe SELECT. `authenticated` recebe SELECT, DELETE e INSERT/UPDATE
somente nas colunas listadas no SQL. Não recebe TRUNCATE, REFERENCES ou TRIGGER.
`usuario_id`, `oculto_admin`, `status_publicacao`, ID e datas ficam fora dos grants
de escrita; após a nova migração, `status_adocao` também fica sem INSERT/UPDATE
para usuários comuns. A operação de adoção será implementada em outra etapa.
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

## Roteiro Python para validação real do Storage

[tests/validar_storage.py](tests/validar_storage.py) implementa os cenários abaixo
com o SDK Python instalado (verificado localmente com `supabase 2.33.0`), sem
novas dependências. A validação real informada e a limitação conhecida estão
registradas no início deste documento. O ajuste de classificação foi validado
somente offline, sem nova execução remota.
Os [testes offline](../tests/test_validacao_storage.py) usam o SDK real com
`httpx.MockTransport`, além de testes das barreiras de segurança. Não certificam
as políticas remotas nem simulam integralmente o serviço Storage.

Plano local realizado: conferir SQL e contratos do SDK; testar confirmação de
destino e classificações de erro; implementar o fluxo e a recuperação de
respostas perdidas; documentar execução e limpeza; executar a suíte offline.

### GRANTs e Data API

A migração aplicada já concede a `authenticated`:

- `SELECT` em `public.animal_fotos`;
- `INSERT` somente em `animal_id, posicao`;
- `UPDATE` somente em `estado`;
- `EXECUTE` em `public.confirmar_foto(uuid)` e acesso mínimo à auxiliar privada.

O roteiro SQL aprovado também verifica `USAGE` em `public` e privilégios efetivos
de tabela/coluna, inclusive ausência de `DELETE`. Portanto **não é necessário
conceder ALL, INSERT/UPDATE de tabela ou habilitar permissões amplas no painel**.
O schema `public` já está exposto segundo o histórico da Data API; mantenha
`adotae_private` fora de Exposed schemas. A configuração efetiva e o cache do
PostgREST ainda dependem do teste real: o preflight faz `SELECT` autenticado por
A e B em `animal_fotos`, filtrado por UUID aleatório, antes de qualquer INSERT.
Resposta vazia comprova que a consulta foi aceita, sem ler dados preexistentes.
Não comprova sozinha visibilidade de registros; isso é conferido após a reserva.

Se houver erro de tabela ausente/cache ou permissão, interrompa e confira a
exposição de `public`, o cache da Data API e os privilégios efetivos. Não use o
botão de exposição para ampliar GRANTs nem reaplique a migração. O roteiro não
altera configuração da Data API e não exige consultar `storage.objects` por SQL.

### Execução futura, manual e exclusivamente em adotae-testes

Reutilize as contas A e B existentes, distintas e com e-mails confirmados.
Use um terminal interativo sem gravação/transcrição, e configure apenas:

```powershell
$env:ADOTAE_TEST_SUPABASE_URL = "https://<Project-Reference-de-adotae-testes>.supabase.co"
$env:ADOTAE_TEST_SUPABASE_PUBLISHABLE_KEY = "sb_publishable_<chave-publicavel-de-adotae-testes>"
.\.venv\Scripts\python.exe supabase/tests/validar_storage.py
```

Não execute agora como parte dos testes locais. O script não importa Flask nem
carrega `.env`, não aceita chaves secretas/JWTs legados e não usa variáveis da
aplicação como fallback. Recusa URL igual a `SUPABASE_URL` quando já presente no
ambiente. Antes de qualquer conexão, exige que você confira independentemente
no painel de **adotae-testes** e digite `adotae-testes <Project Reference>`.
Exige também `bucket conferido` após a conferência dos três valores acima.
A chave publicável não permite verificar administrativamente o nome do projeto
ou a configuração do bucket; essas duas confirmações humanas são obrigatórias.

Depois, informe e-mail e senha de A e B nos prompts. As senhas ficam ocultas;
se o terminal não puder ocultá-las, o script interrompe. Não passe credenciais
por argumentos. Sessões são independentes, sem persistência ou renovação
automática, e a identidade é confirmada pelo Auth. Nenhuma conta é criada ou
alterada; o encerramento usa somente `scope=local`, sem logout global de A/B.
Um terceiro cliente não possui sessão. Logs do SDK são desativados; respostas,
exceções brutas, senhas, JWTs, chaves e eventuais URLs assinadas não são impressos.

### Cenários e limites

1. A/B consultam a tabela pela Data API. A cria um rascunho fictício com nome
   `TESTE-STORAGE-<uuid>` e reserva a posição 1, omitindo ID, dono e estado.
2. Uploads com prefixo de B, animal forjado, foto sem reserva, extensão incorreta
   ou diretório extra devem falhar. Os identificadores gerados e caminhos exatos
   são registrados antes das tentativas. Nenhuma operação usa outro bucket.
3. A envia uma fixture WebP lossless de 1×1 (34 bytes) ao caminho canônico exato,
   com `content-type=image/webp` e `upsert=false`. Download pendente e assinatura
   de upload devem ser recusados. UPDATE direto para `pronta` também é recusado.
4. A chama `confirmar_foto` passando apenas `p_foto_id`. A linha deve mudar
   somente de `pendente` para `pronta`; a segunda chamada preserva a linha inteira.
   O download próprio deve devolver exatamente os bytes enviados.
5. B/visitante não leem a reserva, não confirmam, não fazem upload no caminho de
   A, não baixam, não assinam a leitura nem excluem o arquivo. A não pode assinar
   leitura/upload, listar objetos, atualizar, fazer upsert, copy/move ou excluir
   antes de solicitar remoção. Também se testa DELETE SQL negado e B sem UPDATE.
   Após tentativas destrutivas, A confere a linha e os bytes novamente.
6. A solicita `removendo`, confere o estado, tem nova confirmação e download
   recusados e exclui pela API Storage. Exige retorno de exatamente um objeto,
   com o caminho esperado. A repetição da exclusão deve retornar lista vazia;
   a reserva permanece em `removendo`.

Somente negações Storage conhecidas contam como bloqueio. Leituras/assinaturas
podem ocultar o objeto com código de ausência conhecido. Rede, JWT inválido,
bucket ausente, limites de requisição, MIME inválido e erro interno reprovam o
cenário. Respostas vazias de DELETE/listagem podem ser bloqueio por RLS, mas
**não provam exclusão física**. Antes da remoção, os bytes continuam sendo
conferidos; na remoção autorizada, exige-se o objeto no retorno da API.
Se o serviço instalado retornar códigos/formato diferentes, a execução reprova
para revisão local; não ampliar indiscriminadamente os erros aceitos.

Para diagnosticar essa falha em uma repetição manual, após a primeira confirmação
e a gravação de `estado=pronta`, o terminal registra `CHECKPOINT INÍCIO` e
`CHECKPOINT OK` para `segunda_rpc`, `conferencia_reserva` e `download_proprio`
(inclui a comparação dos bytes). Se uma dessas operações falhar, inclusive por
exceção inesperada ou Ctrl+C, a reprovação informa `etapa` e `tipo`, acrescentando
somente status HTTP válido e códigos de uma lista conhecida quando disponíveis.
Mensagens brutas, argumentos, respostas e URLs não entram nesses diagnósticos;
códigos desconhecidos são omitidos. Exceções inesperadas fora desses três
checkpoints também informam o tipo, sem atribuir uma etapa específica.
O manifesto e o fluxo de limpeza permanecem iguais. Compartilhe somente as
linhas dos checkpoints e da reprovação; não envie credenciais nem respostas da
API. Esta alteração foi verificada apenas offline e não determina a causa
remota da primeira execução.

Não são testados processamento de imagens, resistência a arquivos maliciosos,
limites de tamanho/MIME do bucket, concorrência, publicação, S3, TUS, transformações
ou consumo de URLs assinadas. A assinatura deve ser recusada, não utilizada.
O teste de visitantes usa download sem sessão pelo SDK; não faz GET separado na
rota de URL pública. A RPC verifica associação/metadados, sem decodificar o WebP.

### Manifesto e limpeza controlada

Antes das escritas, o script grava e anuncia
`supabase/tests/storage-runs/<uuid>.json`, ignorado pelo Git. Contém somente
referência do projeto, nome exclusivo, UUIDs A/B/animal/foto, caminhos candidatos,
estados e marcos da execução; **não contém credenciais**. Guarde-o até a limpeza.
Atualizações são feitas por substituição de arquivo temporário. Se a persistência
falhar, a próxima operação é interrompida. Se perder uma resposta de INSERT,
recupera o animal pelo nome exato e dono A e as reservas desse animal.

No sucesso, o arquivo canônico já foi excluído pela API. Em falha/interrupção,
`finally` tenta recuperar a reserva da posição 1, solicitar `removendo` e remover
somente seu caminho exato pela API Storage, sem DELETE SQL. Um retorno vazio é
registrado como ausência física não comprovada. Se uma tentativa negativa tiver
sido aceita indevidamente, não se tenta varrer diretórios: os caminhos candidatos
ficam no manifesto para conferência e limpeza manual. Falha, expiração da sessão,
perda de rede ou encerramento forçado podem deixar arquivos/registros pendentes.

**Sempre haverá limpeza SQL pendente após o fluxo completo.** A não possui
DELETE em `animal_fotos`; a FK `NO ACTION` impede excluir o animal ainda vinculado.
O script não recebe credenciais administrativas, não gera DELETE automático,
não libera posição e não promete rollback entre serviços.

Plano para uma etapa posterior, separada e explicitamente autorizada:

1. No painel, confirmar novamente **adotae-testes** e o manifesto desta execução.
   Conferir por igualdade o nome `TESTE-STORAGE-<uuid>`, dono A, `animal_id`,
   `foto_id`, posição e estados; se faltarem IDs por perda de resposta, localizar
   pelo **nome completo e dono**, depois registrar os IDs exatos. Não usar LIKE,
   prefixos, exclusão em lote de usuários ou limpeza de outras execuções.
2. Conferir a resposta da API para exclusão do arquivo canônico e todos os
   caminhos negativos listados. Se houver incerteza, inspecionar esses caminhos
   exatos no painel Storage. Eventual arquivo residual deve ser removido pela
   API/painel Storage em ação separada e autorizada; nunca por SQL em
   `storage.objects`. Se a política impedir a limpeza pelo usuário, interromper
   e planejar essa intervenção, sem ampliar GRANTs ou fornecer chaves ao script.
3. Somente após comprovar ausência dos arquivos e encerrar a execução, um
   operador autorizado poderá fazer a limpeza controlada das tabelas públicas:
   em transação, conferir/reservar as linhas, apagar primeiro **somente as
   reservas identificadas** (IDs exatos + animal + dono + nome conferidos), depois
   o animal por ID + dono + nome, exigindo as contagens esperadas. Interromper e
   dar ROLLBACK em qualquer divergência ou vínculo inesperado. Revisar os
   registros afetados antes de COMMIT. Essa etapa não está automatizada nem
   autorizada para execução nesta preparação.
4. Conferir ausência desses IDs e dos caminhos, guardar o resultado e só então
   descartar o manifesto local.

O retorno **0** acompanha **APROVADO** ou **APROVADO COM RESSALVA**, conforme os
critérios de cache acima, com encerramento das sessões confirmado e
**limpeza SQL ainda pendente**. Retorno **1** indica reprovação/interrupção;
leia o manifesto e planeje pendências antes de repetir. Uma nova execução usa
novos identificadores e não limpa automaticamente uma execução anterior.

Após a execução futura, remova as duas variáveis temporárias do terminal com
`Remove-Item Env:ADOTAE_TEST_SUPABASE_URL` e
`Remove-Item Env:ADOTAE_TEST_SUPABASE_PUBLISHABLE_KEY`.

### Diagnóstico da adoção real — validado em adotae-testes em 10/11/2026

Conforme os resultados informados, **as 12 verificações reais foram aprovadas
em 10/11/2026 no Supabase adotae-testes**. A operação usou a Data API, URL e chave
publicável do projeto e contas fictícias, sem `service_role`. O registro abaixo
documenta essa execução; nenhuma operação remota foi realizada nesta atualização.

`supabase/tests/validar_adocao.py` reutiliza os controles dos roteiros anteriores:
somente `ADOTAE_TEST_SUPABASE_URL` e `ADOTAE_TEST_SUPABASE_PUBLISHABLE_KEY`,
chave `sb_publishable_`, sem `.env`, Flask, SQL administrativo ou `service_role`.
Não aplica migrações, altera políticas/grants nem cria ou exclui fixtures.
A migração `202611100001_marcar_adotado.sql` foi informada como aplicada pelo
operador em **adotae-testes**, onde a validação real foi concluída.

Antes de executar, confirme no painel a referência do projeto e use contas
**fictícias comuns**, distintas, A e B, com e-mails confirmados. Pela interface,
identifique dois anúncios de A sem uso importante ou compartilhado e anote seus
UUIDs exatos: um publicado, disponível, visível e com fotografia pronta; outro
rascunho disponível. Se não existirem, **crie-os pela interface antes do teste**.
O script não procura anúncios por nome nem escolhe automaticamente um registro.
O roteiro exige a indicação manual dos UUIDs das fixtures.

Em PowerShell, em terminal interativo privado, sem gravação/transcrição:

```powershell
$env:ADOTAE_TEST_SUPABASE_URL = 'https://<referencia-de-adotae-testes>.supabase.co'
$env:ADOTAE_TEST_SUPABASE_PUBLISHABLE_KEY = 'sb_publishable_<chave-publicavel-de-testes>'
.\.venv\Scripts\python.exe supabase/tests/validar_adocao.py
```

Esses valores são placeholders; não use as variáveis da aplicação nem credenciais
administrativas. A URL e a chave devem corresponder ao mesmo **adotae-testes**.
Confirme a referência independentemente no painel: a chave publicável não informa
o nome do projeto. As credenciais das duas contas são solicitadas no terminal,
com senha oculta e validação da identidade no Auth; não são salvas.

O roteiro pede `AUTORIZAR adotae-testes <referência>` **antes de criar clientes**:
isso autoriza login, leituras, assinaturas públicas de controle e tentativas
negativas. Depois exige `FIXTURES DESCARTAVEIS` e os dois UUIDs. Até as tentativas
negativas podem alterar uma fixture se a proteção estiver incorreta; por isso
ambos os registros devem ser descartáveis. Não execute sem essa autorização.

Resultados informados das **12 verificações aprovadas no Supabase real**:

1. A RPC foi recusada para o visitante com `42501`.
2. A RPC sobre o anúncio publicado de A foi recusada para B, outro proprietário,
   com `42501`.
3. A RPC sobre o próprio rascunho foi recusada para A com `42501`.
4. O UPDATE direto de `status_adocao` por A foi recusado com `42501`.
5. Após **cada** tentativa recusada, os dois registros inteiros, todas as reservas
   e os bytes das fotografias prontas permaneceram preservados. O controle positivo
   anterior confirmou catálogo, detalhes, metadados e assinatura pública disponíveis.
6. A chamada positiva à RPC foi realizada com sucesso pelo proprietário A,
   após a autorização explícita da adoção do anúncio indicado.
7. A consulta como A confirmou `status_adocao = 'adotado'`.
8. Somente `status_adocao` e `atualizado_em` mudaram no anúncio; a data avançou.
   Os demais campos, o rascunho, as reservas e os bytes das fotografias prontas
   foram preservados.
9. A consulta anônima do catálogo, filtrada pelo UUID, retornou zero linhas:
   o anúncio adotado deixou de aparecer no catálogo anônimo.
10. A consulta anônima de detalhes por UUID, sem filtro de status, e a dos
    metadados das fotografias retornaram zero linhas; detalhes e metadados
    públicos ficaram inacessíveis após a adoção.
11. Novas assinaturas públicas anônimas de **todas** as fotos prontas foram
    recusadas por permissão ou ausência conhecida. Rede, sessão expirada,
    RPC/bucket ausente, limite de requisições e erros internos não contam como
    bloqueio aprovado.
12. A segunda chamada como A foi recusada com `42501`, sem alteração adicional
    dos registros, reservas ou bytes em comparação ao estado após a adoção.

Retorno **0**: etapas 1–12 aprovadas nessa execução. Retorno **2**: etapas 1–5
aprovadas, adoção não autorizada e etapas 6–12 **não executadas**. Retorno **1**:
falha, interrupção ou encerramento de sessão inconclusivo. Não há repetição
automática, reversão ou limpeza. Se a resposta da adoção se perder, consulte o
registro como A antes de qualquer nova tentativa: a transação pode ter concluído.

Snapshots e bytes ficam somente em memória. Os resultados podem ser registrados
pelas linhas `OK`, `PARCIAL`, `APROVADO` ou `NÃO APROVADO`, sem compartilhar a
entrada de credenciais. URLs assinadas de controle duram 60 segundos, não são
impressas, persistidas nem consumidas. O roteiro verifica **novas assinaturas**;
não comprova invalidação de URLs já emitidas nem de caches. A comparação abrange
as duas fixtures e suas fotos, sem acesso a outras linhas, metadados internos
de `storage.objects`, `xmin` ou `ctid`.

**Verificação Flask separada:** continuam pendentes a ação de marcar como
adotado, sua autorização/CSRF, as mensagens e a atualização da interface após
a adoção. Catálogo e detalhes já existentes requerem conferência HTTP/browser
separada; as etapas 9–10 acima validam as consultas pela Data API, não as páginas
Flask. Este script não implementa interface nem declara essas verificações
aprovadas.

Verificação exclusivamente offline do roteiro:

```powershell
.\.venv\Scripts\python.exe -m unittest tests.test_validacao_adocao -v
.\.venv\Scripts\python.exe -m unittest discover -v
```

Depois de executar manualmente, remova as variáveis temporárias do terminal:

```powershell
Remove-Item Env:ADOTAE_TEST_SUPABASE_URL
Remove-Item Env:ADOTAE_TEST_SUPABASE_PUBLISHABLE_KEY
```

Para validar **somente localmente**, sem operações remotas:

```powershell
.\.venv\Scripts\python.exe -m unittest tests.test_validacao_storage -v
.\.venv\Scripts\python.exe -m unittest discover -v
```
