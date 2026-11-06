# Adotaê

Projeto acadêmico de Engenharia de Software com o objetivo de facilitar a adoção responsável de animais.

## Etapa atual

Esta primeira versão apresenta o projeto e um catálogo com quatro animais fictícios. O site se adapta a celulares e computadores. O catálogo é demonstrativo: ainda não é possível cadastrar animais ou solicitar uma adoção.

Em `/animais`, é possível combinar filtros por tipo de animal, porte e distrito. Use “Aplicar filtros” para consultar e “Limpar” para voltar ao catálogo completo. Os filtros são enviados por parâmetros GET na URL.

Cada cartão oferece “Ver detalhes”, que abre `/animais/<id>` com a imagem, os dados e a descrição do animal, além de um link de volta ao catálogo. A seção de contato é apenas demonstrativa, sem contatos ou ações funcionais. Identificadores inexistentes retornam HTTP 404.

Em `/cadastro`, é possível criar uma conta com nome, e-mail e senha de pelo menos 8 caracteres pelo Supabase Auth. O nome é enviado nos metadados `nome`. O formulário valida os dados no servidor e possui proteção CSRF. Após o envio, orienta a confirmação do e-mail, sem iniciar sessão.

O fluxo de confirmação pela rota `/auth/confirm` foi validado manualmente: o servidor valida o `token_hash` com o Supabase e redireciona para a página inicial, sem parâmetros na URL. Uma mensagem temporária informa o resultado abaixo do cabeçalho; em caso de sucesso, oferece um link para entrar. A confirmação não inicia login nem guarda os tokens retornados. Links inválidos, expirados ou já utilizados não mostram sucesso.

Em `/login`, usuários com e-mail confirmado podem entrar. A sessão permanece entre requisições, com renovação dos tokens expirados quando possível. `/minha-conta` exige autenticação e apresenta uma saudação e o botão “Sair”, que envia um POST protegido por CSRF. A navegação oferece “Entrar” ou “Minha conta” conforme a identidade validada. Recuperação de senha, edição de perfil e gerenciamento de anúncios ainda não foram implementados.

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
antes da API pública definitiva. Publicação e moderação ainda não têm mecanismo
implementado.

## Tecnologias

- Python e Flask
- HTML, CSS e JavaScript
- Supabase Auth (SDK oficial Python), python-dotenv e Flask-WTF

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

Nesta revisão, a suíte local passou com 74 testes (o histórico anterior informava
70), incluindo os testes offline do
[roteiro da Data API](supabase/tests/validar_data_api.py), em
[tests/test_validacao_data_api.py](tests/test_validacao_data_api.py).
Esses testes usam mocks e não comprovam as políticas instaladas no Supabase.
Os resultados remotos são registrados separadamente no README do banco.
