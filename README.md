# Adotaê

Projeto acadêmico de Engenharia de Software com o objetivo de facilitar a adoção responsável de animais.

## Etapa atual

Esta versão apresenta o projeto e um catálogo com quatro animais fictícios. O site se adapta a celulares e computadores. O catálogo permanece demonstrativo; os novos cadastros são salvos separadamente como rascunhos.

Em `/animais`, é possível combinar filtros por tipo de animal, porte e distrito. Use “Aplicar filtros” para consultar e “Limpar” para voltar ao catálogo completo. Os filtros são enviados por parâmetros GET na URL.

Cada cartão oferece “Ver detalhes”, que abre `/animais/<id>` com a imagem, os dados e a descrição do animal, além de um link de volta ao catálogo. A seção de contato é apenas demonstrativa, sem contatos ou ações funcionais. Identificadores inexistentes retornam HTTP 404.

Em `/cadastro`, é possível criar uma conta com nome, e-mail e senha de pelo menos 8 caracteres pelo Supabase Auth. O nome é enviado nos metadados `nome`. O formulário valida os dados no servidor e possui proteção CSRF. Após o envio, orienta a confirmação do e-mail, sem iniciar sessão.

O fluxo de confirmação pela rota `/auth/confirm` foi validado manualmente: o servidor valida o `token_hash` com o Supabase e redireciona para a página inicial, sem parâmetros na URL. Uma mensagem temporária informa o resultado abaixo do cabeçalho; em caso de sucesso, oferece um link para entrar. A confirmação não inicia login nem guarda os tokens retornados. Links inválidos, expirados ou já utilizados não mostram sucesso.

Em `/login`, usuários com e-mail confirmado podem entrar. A sessão permanece entre requisições, com renovação dos tokens expirados quando possível. `/minha-conta` exige autenticação e apresenta uma saudação e o botão “Sair”, que envia um POST protegido por CSRF. A navegação oferece “Entrar” ou “Minha conta” conforme a identidade validada. A listagem dos próprios anúncios já funciona em `/minha-conta`; edição de anúncios publicados, exclusão, recuperação de senha e edição de perfil continuam pendentes.

Em `/minha-conta`, o link “Anunciar animal” abre `/anunciar`. Usuários autenticados com e-mail confirmado podem preencher os dados do animal e salvar um rascunho no Supabase, com uma fotografia opcional e contatos opcionais. O formulário usa Flask-WTF, CSRF e validação no servidor, reutilizando as opções do catálogo. Campos opcionais vazios viram NULL; o detalhe de espécie é obrigatório apenas para “Outro animal doméstico”. Após o envio, a aplicação redireciona para a conta com uma mensagem temporária de sucesso ou aviso de falha parcial. Publicação, moderação e exclusão ainda não estão disponíveis.

A seção “Meus anúncios” em `/minha-conta` consulta a Data API com o cliente autenticado da requisição, filtro explícito pelo proprietário e ordem de criação decrescente. Exibe nome, espécie, distrito e status de publicação, sem fotos, com a opção “Editar” somente nos rascunhos. Lista vazia e falha de consulta têm mensagens distintas; em caso de indisponibilidade ou resposta inválida, a página preserva o logout e retorna HTTP 503. Os testes usam o SDK real com HTTP simulado. A listagem também foi validada manualmente no site conectado ao Supabase de testes, com resultados satisfatórios.

Em `/anuncios/<uuid>/editar`, o proprietário autenticado pode editar os dados de seus rascunhos, com as mesmas validações do cadastro e proteção CSRF. As fotografias permanecem intactas. Leitura e UPDATE filtram UUID, proprietário e status de rascunho; apenas os campos permitidos são enviados. A confirmação exige retorno válido com os dados salvos. Atualização sem registros retorna HTTP 409; falhas ou respostas inesperadas retornam HTTP 503, sem mensagem de sucesso. Anúncios inexistentes, de terceiros ou publicados retornam HTTP 404. As respostas usam `Cache-Control: no-store`. O fluxo foi validado offline com o SDK real e HTTP simulado.

Na opção “Fotografia” dos rascunhos em `/minha-conta`, a página
`/anuncios/<uuid>/foto` permite adicionar a primeira fotografia a um rascunho
próprio, sem criar outro anúncio. Qualquer reserva `pendente`, `pronta` ou
`removendo` bloqueia novo envio; falhas de consulta também bloqueiam o formulário.
Reutiliza o processamento e o upload de `/anunciar`, com limite HTTP de 4 MiB,
CSRF e `Cache-Control: no-store`. Só informa sucesso após consultar a reserva
como `pronta`. Falhas parciais mantêm reservas e arquivos para recuperação futura,
sem reenvio automático. Não há pré-visualização, substituição ou exclusão.
Este fluxo foi validado apenas por testes offline com HTTP simulado.

O INSERT usa o cliente isolado da requisição: chave publicável em `apikey` e JWT do usuário validado (ou renovado) em `Authorization`, respeitando a RLS. Envia apenas as colunas permitidas, deixando proprietário, estados, ID e datas para o banco. O sucesso exige retorno de um registro com UUID válido, estado `rascunho` e proprietário igual ao usuário autenticado. Esta implementação foi testada offline com o SDK real e HTTP simulado. Conforme a validação manual realizada em `adotae-testes`, o login com conta `.test` e o cadastro de um animal fictício pelo navegador funcionaram; o registro foi confirmado no PostgreSQL com proprietário e estados iniciais corretos. A consulta anônima pela Data API não retornou o rascunho. O registro fictício foi excluído e a tabela de testes ficou novamente vazia.

A migração de `public.animais` foi aplicada em `adotae-testes` e no projeto
principal. No projeto de testes, foram aprovados cenários manuais de RLS,
privilégios e constraints e testes reais pela Data API com chave publicável,
dois usuários distintos e acesso anônimo. A limpeza temporária foi concluída.
No principal, a auditoria após correção dos privilégios passou em 10 de 10
verificações, e a leitura anônima pela API retornou zero registros sem erro.
A escrita autenticada pela API ainda não foi testada no principal.
O catálogo continua demonstrativo, sem integração ao banco.

Consulte [supabase/README.md](supabase/README.md) para os arquivos e limites.
Habilitar tabelas na Data API pode alterar privilégios SQL e exige nova auditoria.
A leitura pública permite expor `usuario_id`; essa decisão precisa de revisão
antes da API pública definitiva. A migração SQL de publicação direta foi aplicada
e validada em `adotae-testes` em 08/11/2026, com testes remotos funcionais e pontuais,
sem reexecução integral da suíte SQL local. Ainda não há botão ou rota Flask.
Moderação continua pendente.

O módulo `app/fotos.py` oferece `processar_foto(arquivo)` para um `FileStorage`
do Flask, retornando `FotoProcessada` com `conteudo` (bytes), `content_type`,
`largura` e `altura`. O tamanho para upload é `len(resultado.conteudo)` e a
extensão é `.webp`. Erros de validação geram `FotoInvalida` com mensagem amigável.
O processamento é em memória: valida o conteúdo JPEG/PNG/WebP de até 3 MiB,
corrige EXIF, preserva transparência, remove metadados e limita o maior lado a
1600 pixels e a saída a 512.000 bytes. Rejeita animações, mais de 20 megapixels
ou lados acima de 10.000 pixels antes de decodificar. Tenta qualidades de 85 a
45 e, se necessário, reduz proporcionalmente as dimensões em 20%, até o maior
lado chegar a 320 pixels; se ainda não couber, rejeita. Imagens menores não são
ampliadas. `/anunciar` processa a fotografia antes de criar o animal e aceita
somente uma por envio, com corpo HTTP limitado a 4 MiB. Usa exclusivamente o
cliente autenticado para reservar a posição 1, enviar WebP ao bucket privado
`animal-fotos` sem sobrescrita e chamar `confirmar_foto`. O caminho usa os IDs
reais de usuário, animal e reserva. O sucesso exige também a leitura do estado
`pronta`; não há exibição de fotos no catálogo demonstrativo.

Se a reserva, o upload ou a confirmação falhar, o rascunho permanece e a conta
exibe um aviso específico, sem anunciar a foto como adicionada. Respostas
perdidas podem deixar uma reserva pendente, um arquivo vinculado sem confirmação
ou uma confirmação concluída cujo resultado não foi recebido. Não há exclusão
ou repetição automática dessas operações. A reserva continua ocupando a posição
e a recuperação ainda não está disponível pela interface; reenviar `/anunciar`
criaria outro anúncio. O upload usa cache-control zero, sujeito à
[ressalva de cache documentada](supabase/README.md#validação-do-storage-e-limitação-de-cache).
Esta integração foi validada apenas por testes offline.

## Tecnologias

- Python e Flask
- HTML, CSS e JavaScript
- Supabase Auth (SDK oficial Python), python-dotenv e Flask-WTF
- Pillow para processamento de fotografias em memória

## Como executar

Com o Python 3.10 ou superior instalado, abra o terminal na pasta do projeto.

No Windows (PowerShell):

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe run.py
```

No Linux ou macOS:

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python run.py
```

A criação do ambiente e a instalação das dependências são necessárias apenas na primeira vez. Se o ambiente `.venv` já existir, pule o primeiro comando.

Depois, abra **http://localhost:5000** no navegador. Para encerrar, pressione `Ctrl+C` no terminal.

### Configurar cadastro e login

Copie `.env.example` para `.env` e preencha `SUPABASE_URL`, `SUPABASE_PUBLISHABLE_KEY` (chave publicável, nunca administrativa) e `SECRET_KEY`. Se já existir um `.env`, acrescente apenas as variáveis ausentes. Gere um segredo aleatório com `python -c "import secrets; print(secrets.token_hex(32))"` e use o mesmo valor em todas as instâncias da aplicação. Não publique o `.env`. Sem `SECRET_KEY`, os formulários e a conta ficam indisponíveis; sem as variáveis Supabase, as operações de autenticação ficam indisponíveis.

Para executar em HTTP local, acrescente `ADOTAE_LOCAL_HTTP=true` ao `.env`. Em produção, omita essa variável ou use `false` e utilize HTTPS. Os cookies usam `Secure` por padrão; na Vercel, esse atributo é obrigatório mesmo que a opção local esteja presente. Configure as variáveis no ambiente da hospedagem. A implantação na Vercel ainda não foi validada.

No painel de um projeto gratuito do Supabase, habilite o provedor de e-mail e o cadastro de novos usuários, mantenha **Confirm email** ativado e configure **Site URL** com `http://localhost:5000` durante o desenvolvimento (a URL pública quando houver hospedagem), sem barra final. Em **Authentication → Notifications → Emails → Confirm sign up**, substitua o link que usa `{{ .ConfirmationURL }}` por:

```html
<a href="{{ .SiteURL }}/auth/confirm?token_hash={{ .TokenHash }}&amp;type=email">Confirmar meu e-mail</a>
```

O servidor usa `verify_otp({"token_hash": ..., "type": "email"})` com um cliente isolado, sem persistência nem renovação automática. Consulte a referência oficial de [verificação OTP em Python](https://supabase.com/docs/reference/python/auth-verifyotp) e as [variáveis dos templates de e-mail](https://supabase.com/docs/guides/auth/auth-email-templates). Essa alteração no painel é manual; o código não modifica configurações do Supabase. E-mails antigos mantêm o link anterior.

A rota de confirmação responde com `Cache-Control: no-store` e `Referrer-Policy: no-referrer`. O log de acesso local do Werkzeug oculta os parâmetros dessa rota. Na hospedagem ou em um proxy, configure também os logs de acesso para omitir a query de `/auth/confirm`, pois essas camadas recebem o link antes do Flask.

O projeto principal do Supabase está configurado com SMTP personalizado via Gmail e template de confirmação em português. O envio foi testado com um endereço externo à equipe do Supabase: o e-mail foi recebido e a confirmação foi concluída com sucesso, mas a mensagem foi direcionada à pasta de spam. O Gmail pode apresentar limitações de entrega; o uso de um serviço transacional gratuito poderá ser avaliado posteriormente. Consulte as [limitações oficiais de envio](https://supabase.com/docs/guides/auth/auth-smtp).

### Segurança e limites da sessão

Os tokens de login ficam em cookies exclusivos `HttpOnly`, `SameSite=Lax`, sem domínio compartilhado. Não são armazenados na sessão assinada do Flask, que guarda o segredo CSRF e os avisos temporários, sem credenciais. Os cookies de autenticação duram a sessão do navegador; a validade dos tokens é controlada pelo Supabase. Eles não são criptografados pela aplicação: dependem de HTTPS e da proteção do navegador e devem ser tratados como credenciais.

Cada requisição com autenticação usa um cliente Supabase isolado, restaura ou renova a sessão com `set_session` e valida a identidade no servidor com `get_user`. Respostas autenticadas e páginas de autenticação usam `Cache-Control: no-store`. Não há estado de autenticação em disco ou compartilhado entre instâncias, nem novas tabelas ou dependências.

O logout solicita o encerramento apenas da sessão atual (`scope=local`) e remove os cookies locais mesmo se o serviço estiver indisponível. Nessa situação, o site informa que não conseguiu confirmar o encerramento remoto. Tokens de acesso já emitidos podem continuar válidos até expirar, conforme o funcionamento do Supabase. Requisições simultâneas durante a rotação dos tokens podem disputar a atualização dos cookies; se a sessão não puder ser recuperada, será necessário entrar novamente. A validação remota também adiciona latência e depende da disponibilidade do provedor. Consulte a documentação de [sessões no servidor](https://supabase.com/docs/guides/auth/server-side/advanced-guide) e [logout](https://supabase.com/docs/reference/python/auth-signout).

## Testes

Os testes verificam páginas, catálogo, arquivos estáticos, cadastro, confirmação de e-mail, login, sessão válida e expirada, renovação, logout, CSRF, isolamento entre usuários e ausência de credenciais no HTML, URLs, logs e sessão Flask. Os testes de autenticação usam mocks; os de login e confirmação exercitam o SDK real com transporte HTTP simulado, sem chamadas ao Supabase ou credenciais reais. A confirmação cobre sucesso, links inválidos, expirados ou utilizados, falha do serviço e ausência de login automático. Para executá-los no Windows:

```powershell
.\.venv\Scripts\python.exe -m unittest discover -v
```

No Linux ou macOS, use `.venv/bin/python -m unittest discover -v`.

Os testes incluem adição de fotografia a rascunhos existentes (reservas em todos
os estados, confirmação, conflitos, respostas inesperadas, falhas parciais, CSRF
e limites HTTP), cadastro
e edição dos próprios rascunhos (validação, CSRF, payload, JWT, titularidade, status, falhas, respostas inesperadas e preservação das fotos e do catálogo) e os testes offline do
[roteiro da Data API](supabase/tests/validar_data_api.py), em
[tests/test_validacao_data_api.py](tests/test_validacao_data_api.py).
Esses testes usam mocks e não comprovam as políticas instaladas no Supabase.
Os resultados remotos são registrados separadamente no README do banco.
Os testes de fotografias em `tests/test_fotos.py` geram imagens em memória e
cobrem formatos, transparência, EXIF, redimensionamento, corrupção e limites,
sem serviços externos.
