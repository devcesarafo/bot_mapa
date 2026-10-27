# Bot de Hunt – PASSAGEM para o próximo chat (leia este primeiro)

*Atualizado em 26/10/2026, versão 4 (substitui as anteriores). **Fases 1 a 4 concluídas e utilizáveis em lure real** (G/F/C, R em rajada, revive, mover/parar o Ditto com ajuste e R de emergência). Usuário: "perfeito, deu pra jogar, ficou utilizável". O guia de uso e das constantes está em `Bot_de_Hunt_-_GUIA_de_uso_v4.md`.*

## 1. Como trabalhar com este usuário

- Fala **português (BR)**. Respostas diretas e curtas; quer **gastar poucos créditos**.
- Testa jogando e manda **prints e logs**. Valoriza **dados medidos** e honestidade sobre **limites** ('não testei no jogo real').
- Avança **em etapas pequenas**, cada uma testada antes da próxima. Aceita ajustes pequenos e específicos.
- **Nunca afirmar sem validar** (nos prints/gravações dele). Ao fim de cada etapa: atualizar este documento.
- Ele testa **direto em lure real, acompanhando**, com a parada de emergência à mão.

## 2. Objetivo e regra de ouro

Bot em Python (Windows) para o jogo **PokeAlliance** (janela 1920×1080; o jogo roda como **administrador**, então o terminal também). Percorre uma hunt em laço de **lures**: puxa Gogoats até um ponto de parada, o **Ditto** os mata com a macro **R**, revive, repete.

**Regra de ouro: o treinador nunca pode morrer** (um golpe de Gogoat mata). Falha de leitura **nunca** pode parecer 'seguro'. Automatizar jogo pode violar termos de uso; o risco é do usuário.

## 3. Arquivos (todos em Python; pasta `bot_mapa`)

| Arquivo | Para quê | Estado |
| --- | --- | --- |
| `bot_fase1.py` | **Bot de combate completo:** G, F, C (fase 1), R em rajada (fase 2), revive `e` (fase 3) e **Ditto `o`/`p` com ajuste (fase 4, flag `--ditto`)**. Parada Shift+F. Flags: `--seco` (só loga), `--sem-revive`, `--ditto`, `--lures caminho`, `--diagnostico [print.png]` | **Usável em lure real** |
| `ditto_ctl.py` | **Fase 4.** Máquina de estados do Ditto (thread própria, 40 Hz, lê só a coordenada): P depois do revive, espera do teleporte, rajada de O, P na parada, ajuste (mergulho/retirada), bloqueio do R (`bloqueia_R`), `ditto_perto()` (vê o Ditto reaparecer) | Usável |
| `alvo_ditto.py` | **Fase 4.** `AlvoDitto`: alvo do O pelo **mapa de obstáculos + rota** (tile aberto mais longe, ou perto da parada), `capacidade`, `mergulho`, `retirada`, `ajuste_lure`, `alcance`/`problemas` (ETA, hoje só diagnóstico) | Usável |
| `analisar_ditto.py` | Lê uma gravação do `gravador_quadros.py` e extrai o padrão de uso de `o`/`p` do usuário (`analise_ditto.json`) | Usado 1× (24/10) |
| `monitor_combate.py` | Cerebro (regras de combate) + janela dry-run com 4 cartões. Expõe `acao_def`, `acao_cura`, `acao_R`, `R_tipo`, `area_esgotada`, `revive_tipo`, `sem_monstros_s`, `n_loc`, `n_fora`, `stun_dbg`; método `esquecer_ditto()` | Aprovado |
| `barra_skills.py` | Cooldown de F, G, C, F2, F3, F4 pelo brilho; revive = barra preta | Validado |
| `battle_list.py` | Lê a Battle list; `calibrar()` acha o painel sozinho | Validado |
| `detector_stun.py` | Caveiras, anéis de stun, `achar_ditto`, `caveiras_do_mapa`, `achar_ditto(..., mapa=)` | Validado |
| `leitor_coord.py` | Lê `X, Y, Z` do minimapa | Validado |
| `layout.json` | Regiões; **`battle_list` = [5, 62, 185, 835]** | Atualizado |
| `player_lures.py`, `gravador_lures.py`, `editor_mapa.py`, `lures.json`, `mapa_obstaculos.json` | Andar o laço (A*+WASD), gravar trajeto, mapa de obstáculos. O `bot_fase1 --ditto` usa `montar_rota` do player, o `ObstacleMap` e o `lures.json` mais recente de `gravacoes\` | **Andou o laço no jogo real** (o usuário ainda anda o lure à mão com o bot ao lado) |
| `atalhos.py`, `gravador_quadros.py`, `separar_amostra.py`, `teste_teclas.py` | Atalhos/`Vigia`, gravação de quadros, diagnóstico | OK |
| `envio_teclas.py` | Citado antes, **não veio no zip**. O bot usa `pynput` direto (funciona como administrador) | — |

## 4. Fatos do jogo (medidos)

- Tile ≈ 72,3 px; visão 21×11 tiles; personagem no centro. Treinador: passo reto 316 ms; Gogoat: 2 tiles/s.
- **Stun (Whirlwind, F2):** área 7×7 sem os cantos (3 tiles para cada lado), ~6,2 s, CD 40 s. Sinal: **anel branco que PISCA a ~5 Hz** por monstro.
- **Skills de área do macro R: F2, F3 e F4** (informado pelo usuário). **Com as três em recarga o R não adianta**; só um **revive** reseta tudo. O F3 volta a ficar pronto em poucos segundos; F2/F4 demoram mais.
- **R:** macro do jogo. **C logo antes do R** atrasa o macro (0,6 a 0,8 s).
- **Skills:** F defesa 12 s (CD 40), G velocidade 10 s (CD 40), C cura (CD 100, vida do Ditto ≤ 45%), revive ~1 s reseta tudo; Ditto some da Battle list 0,4 a 0,8 s no revive.
- Parar o Ditto: **parar** e **mover** (leva o Ditto ao cursor). Usuário repete o mover ~4,5×/s durante o lure.
- Tempos: 1º R vem 2,7 a 5,0 s depois de o personagem parar; lure ~20 s; laço ~100 s.
- Atalhos do **bot**: F=`f`, G=`g`, C=`c`, R=`r`, revive=`e`, mover=`o`, parar=`p`. **Parada de emergência: Shift+F** (ignora o `f` do próprio bot; o bot não envia `f` com Shift apertado).
- **HUD (muda com frequência; hoje, 23/10):** Battle list x=5, **y=62** (calibra sozinha); coordenada do minimapa por volta de (1740, 61) (leitura OK pela região do layout); barra de skills: ícones em x F 747, G 782, C 957, F2 992, **F3 1027, F4 1062**, y 952 a 992; área do jogo (200,93)-(1720,887). O jogo teve atualização gráfica: sprites novos, o detector de caveiras (0,93 a 0,98) e o do Ditto continuaram funcionando.
- **Desempenho:** com monstros na tela a visão leva ~260 a 290 ms/quadro → **2,7 a 2,9 leituras/s** no PC dele (sem monstros é rápido: a visão é pulada).
- **Ditto (informado pelo usuário e medido em 24/10):**
  - **Passiva:** o Ditto **entra dentro de monstros**; depois de um tempo o monstro muda de tile (não dá para dar dano no mesmo tile). Não mexer muito nele no fim do lure: movimentos delicados.
  - **Teleporte:** quando o personagem sai da tela do Ditto (**passa de 9 tiles na horizontal ou 4 na vertical**), o Ditto é teleportado para o personagem. Só **depois** disso se manda `o` (antes ele anda e atrasa/impede o teleporte; com o buff G ele anda mais que o personagem).
  - **P depois do revive é intencional:** segura o Ditto no ponto do revive; ao sair da tela ele teleporta, e aí o `o` o manda para a frente.
  - **Ritmo do usuário (1 volta, 5 lures):** `o` a 4,2 a 4,8 Hz; rajadas de 2 a 10 s; P 0,2 a 0,6 s depois do último O e 0,7 a 2,5 s depois de o personagem parar; R 1,9 a 5,4 s depois do P; revive 1,6 a 5 s depois do R. Ele mira o **tile mais longe** (até 10 na horizontal, 5 na vertical, diagonal no canto) na direção do laço, para o Ditto não ficar bloqueado pelos Gogoats atrás.
  - **Janela do cursor:** |dx| ≤ 10 e |dy| ≤ 5 tiles do personagem (centro do tile do personagem em (960, 490) px).
- **Lógica de lure (usuário):** os monstros chegam por um lado e **fecham a área do Ditto da ponta mais perto para a mais longe**; só fecha todos em **lugar aberto** (obstáculo/floresta reduzem os tiles livres da área 7×7, até 44). Com ~16 Gogoats o Ditto pode perder **70% da vida em ~2 s**; o final do lure é crítico.
- **Detecção:** a Battle list às vezes tem mais monstros que o detector localiza na tela (ex.: 13 a 16 na lista contra 10 a 12 localizados; monstros cortados no topo ou sobrepostos).
- **Ocorrência grave (25/10):** o treinador morreu num lure com 16 Gogoats (ajustes repetidos + bloqueios do R atrasaram o R de emergência). Ver erros 17 a 20.

## 5. Regras de combate (Cerebro, constantes no topo do `monitor_combate.py`)

- **ATAQUE (R):** só com o personagem parado há ≥ 2,5 s, nº de monstros estável por 1 s e **todos dentro da área do Whirlwind** (posição do Ditto); espera máxima 5 s. **Parado → regras normais primeiro; emergência (Ditto ≤ 55% com 3+ monstros, ou lure ≥ 20 s) só vale com o personagem ainda andando** (antes ela mascarava a regra normal). "Rajada em curso" (R recente < 2,5 s) continua. **Skills de área esgotadas → NADA.** O cartão expõe `R_tipo` = curso / normal / emergencia / espera_fora.
- **DEFESA:** G cedo (monstro sem stun), F quando o Ditto perde 3% ou aos 8 s de lure.
- **CURA:** C com vida ≤ 45%. **REVIVE (card):** 'liberado' com todos stunados e ≥ 2,5 s de stun; 'resetar' sem monstros com skills gastas; PERIGO com Ditto fora e monstro sem stun. O Cerebro expõe `revive_tipo` = `resetar` / `liberado` / None.
- **Stun com TRAVA:** confirmado vale 6,2 s; cai só com evidência contrária (monstro se mexeu, monstro novo, Ditto levando dano). **Queda de vida do Ditto NÃO cancela a trava durante o revive** (Ditto fora/voltando, até 3 s depois do `e`): a barra muda de valor no revive e isso gerava um [PERIGO] falso.
- **Revive = ciclo novo:** apaga a 'rajada em curso' do lure anterior (por evento `e`, barra preta ou Ditto sumindo/voltando da Battle list).

## 6. O que o `bot_fase1.py` faz hoje (constantes no topo)

- **G, F, C:** confirma cada tecla pela barra (prazo 0,7 s), repete 1×, máx. 2 tentativas, depois trava a skill por 6 s e avisa. A decisão precisa se repetir em 2 leituras. 0,35 s entre teclas. G antes de F.
- **C:** com vida < **40%** passa na frente de G/F/R e nunca espera o R; entre 40% e 45% pode esperar até 1,5 s se o R está recomendado.
- **R:** rajada de **3** (0,25 s ±30%, `HUMANIZAR`), pausa 1 s, confere se uma skill de área entrou em recarga, máx. **12 R por lure**. **Só em R_tipo curso/normal** (parado e todos no alcance). **Nunca** em emergência, nem com monstro fora do alcance, nem com **menos de 3 monstros vivos** (`R_MIN_MONSTROS`; 1 ou 2 = começo do próximo lure), nem com F2+F3+F4 em recarga. Segura o R por 1 s depois de um C. Cancela a rajada se as condições caem.
- **Segurança:** só age com skills legíveis, Battle list achada, Ditto presente, fora de revive e **≥ 2 leituras/s** (`FPS_MIN`). Log na tela e em `monitor_logs\bot_*.txt`; mensagens `[R] NÃO apertado (...)` explicam as recusas.
- **REVIVE (`e`, fase 3), três casos, sempre com leitura válida e Ditto presente:**
  - **resetar:** sem monstros há ≥ **0,6 s** (`REVIVE_SEM_MONSTROS_S`) em 2 leituras seguidas e alguma skill gasta.
  - **cadência:** F2+F3+F4 todas em recarga e **todos stunados com ≥ 3 s de stun** (`REVIVE_STUN_MIN_S`): reseta as skills para bater o R de novo.
  - **crítico:** Ditto ≤ 25% (`DITTO_CRITICO_HP`), C em recarga e todos stunados com ≥ 3 s.
  - **Nunca** com monstro sem stun: só loga `[REVIVE] aguardando ...`. Com Ditto fora e monstro sem stun: `[PERIGO]` + beep; **o bot NÃO foge sozinho** (fuga automática ainda não existe). Se o revive foi apertado com todos stunados, o bot não dá o alarme enquanto o stun garantido durar (`[revive] sem alarme ...`).
  - Confirmação: Ditto some ou skills resetam em ≤ 1,5 s; senão repete 1×, depois trava 10 s. Mín. 6 s entre revives; máx. 3 revives com monstros por lure. O `e` do bot zera a 'rajada em curso' (injeta o evento 'revive').
- **Proteção geral:** erro de leitura em um quadro não derruba o bot (`[ERRO] ...`, quadro ignorado, nenhuma tecla).
- **Não aperta:** pokébola. (`o`/`p` só com `--ditto`, ver abaixo.)

### Fase 4 (flag `--ditto`; usa o MOUSE: o usuário não deve mexer nele durante o lure)
- **Ciclo (ditto_ctl.py):** revive de **fim de lure** (Ditto volta com ≤ 2 monstros) → P assim que o personagem anda (~0,8 s) → personagem passa de 9/4 tiles do ponto do P → **espera o teleporte** (vê o Ditto a ≤ 2,6 tiles do personagem; se não vê, assume em 1,2 s) → rajada de **O a ~4,5 Hz** com o mouse no alvo de `alvo_ditto` → personagem parado → continua O por 0,4 s → **P com 1,6 s de parada** (1,6 s evita P em pausas de 1 a 1,5 s no meio da rota).
- **Alvo do O (alvo_ditto.alvo):** descobre o lure e o ponto da rota; escolhe o tile **andável e aberto mais longe** (até 10/5) perto da rota à frente; quando a parada está na tela, o tile aberto perto dela com **capacidade** para `n_monstros + 1` e entre 3 e 8 tiles do treinador. Tile desconhecido/bloqueado nunca é alvo. Sem mapa/rota usa N tiles à frente do rumo.
- **Ajuste do Ditto no fim do lure** (a partir de 0,8 s depois de parar; pode substituir o P final): **reativo** = monstro parado fora da área há 1,0 s (0,6 s se o Ditto está < 70% ou perdeu ≥ 8%); **antecipado** = a área tem menos tiles andáveis que `n_monstros + 1` → retirada para o aberto antes de os monstros chegarem. Estratégias: **1º MERGULHO** (tile em ≤ 8 tiles que cobre mais monstros, pode ser dentro de monstro, penaliza tirar os já cobertos, nunca a < 4 tiles do treinador) e **2º RETIRADA** (tile mais aberto a 5 a 10 tiles, lado oposto ao grupo); **alterna** se a anterior não resolveu. Execução delicada: 2 a 5 `o` (0,25 s) e `p`; máx. **4 ajustes/lure**, ≥ 2,5 s entre eles.
- **Bloqueio do R (`bloqueia_R`):** Ditto em movimento; 0,5 s depois de um P; depois de um ajuste, até ver o Ditto de novo (máx. 2 s). O Cerebro esquece a posição do Ditto guardada (1,5 s) quando o bot o move.
- **R de emergência do bot** (`R_EMERG_*`): Ditto ≤ 35%, ou ≤ 65% com **vida prevista em 2 s ≤ 10%** (tendência dos últimos 2 s); personagem parado ≥ 2 s; ≥ 60% dos monstros no alcance (40% se muito crítico: ≤ 20% ou previsto ≤ 5%); **só "Ditto em movimento" bloqueia**; 1 leitura basta. **Tem prioridade sobre o ajuste.** Sobram os que estão fora: o usuário foge e revive à mão.
- **R extra:** não sai se o Ditto não foi localizado e houve monstro fora do alcance nos últimos 6 s ('monstros juntos' sem o Ditto não prova que todos estão no alcance).
- **Duplicidade:** uma segunda 'volta do Ditto' em < 3 s (a Ditto piscou na Battle list) é ignorada.

## 7. Erros já cometidos e corrigidos (não repetir)

1. Contar anéis com tolerância deixou um Gogoat sem stun passar → hoje monstro a monstro (caveira + anel).
2. O anel **pisca**: precisa de ≥ 8 leituras/s e janela de 0,5 s; em amostras de 1 quadro/s a ausência do anel não prova nada.
3. O redemoinho azul NÃO é o stun.
4. Tempo entre parar e o R se mede pela **coordenada de todos os quadros**.
5. Battle list vazia por não achar o painel mostrava verde → hoje cinza com aviso.
6. Achar o Ditto por texto/cor do rótulo não funciona; funciona pela barra de vida sem caveira.
7. Teclas não chegam ao jogo: rodar o terminal como **administrador**.
8. **(23/10) HUD novo:** a Battle list subiu de y=195 para y=62; `layout.json` atualizado e o bot calibra ao iniciar.
9. **(23/10) Leitura lenta (2,5/s):** a visão rodava sempre e calculava o template das caveiras 2×. Hoje pula sem monstros, calcula 1×/quadro e roda os anéis em paralelo (resultados idênticos).
10. **(23/10) O bot apertou R na 'emergência'** com o Ditto andando e 7 monstros fora do alcance (matou só metade; usuário teve que fugir). A emergência é recomendação para humano; **o bot nunca a usa**.
11. **(23/10) 'Rajada em curso' puxava Gogoat do lure seguinte** (o novo monstro aparece logo depois do revive/último R). Corrigido: revive zera a 'rajada em curso' e o R exige ≥ 3 monstros.
12. **(23/10) Emergência mascarava a regra normal** (R demorava ou não saía com o Ditto < 55% mesmo com todos juntos): hoje parado → regra normal primeiro.
13. **(23/10) Crash no revive 'resetar':** o log formatava `stun_restante` que vem vazio sem monstros. Hoje formata com segurança e o loop protege contra erros.
14. **(23/10) Revive de reset esperava 1,5 s** sem monstros (proteção minha alta demais) → hoje 0,6 s em 2 leituras. O humanizador só varia o intervalo do R (±30% de 0,25 s) e não afeta o revive.
15. **(23/10) [PERIGO] falso após o revive** (monstro estava stunado, mas a trava caía porque a barra do Ditto muda no revive): corrigido no Cerebro e com a garantia de stun no bot. Hipótese ainda não reproduzida; o log do [PERIGO] agora mostra `stun_dbg` (latch, sem_stun, anéis).
16. **(24/10) A variável `ctl` do teclado foi sobrescrita pelo controle do Ditto** → todas as teclas do bot (G, F, C, R, e) falhavam com `AttributeError` (até sem `--ditto`); o `[ERRO]` limitado a 1 por 2 s escondeu o resto. Hoje o controle do Ditto é `dctl`. **Teste de fumaça (ver guia) pega esse tipo de erro.**
17. **(25/10) O ajuste nunca disparava:** "preso" vinha de um modelo de ETA (caminho livre = vai chegar) que não bateu com o jogo. Hoje "preso" = **parado fora da área há 1,0 s** (evidência).
18. **(25/10) O treinador morreu:** 3 ajustes seguidos + bloqueios do R (movimento/assentar/ver o Ditto) atrasaram o R de emergência até o Ditto estar com 11% (80% → 11% em ~2 s). Hoje: emergência por tendência, só o movimento bloqueia, emergência tem prioridade sobre ajuste.
19. **(25/10) O 'passo à frente' entrou em obstáculo** (capacidade 43 → 28) e foi repetido; os ajustes antigos também puxavam os monstros para o personagem/obstáculos. Substituídos por MERGULHO/RETIRADA (nunca a < 4 tiles do treinador, abertura ≥ 1 ou 2, capacidade checada).
20. **(25/10) R com o Ditto não localizado** (`monstros juntos 5x5`) logo depois de ⚠ monstros fora do alcance. Hoje bloqueia por 6 s.
21. **(24/10) P duplicado** quando o Ditto piscou na Battle list após o revive → ignora uma 2ª volta em < 3 s.
22. **(24/10) O cursor estava perto demais do personagem** (os Gogoats atrás do Ditto bloqueavam o usuário) → hoje o tile mais longe (até 10/5).

## 8. Próximos passos

1. **Fase 5: integrar tudo ao `player_lures.py`** (andar o laço, mover/parar o Ditto, R, revive em um só programa). Hoje o usuário anda o lure à mão e o bot cuida de combate + Ditto; o alvo do O já usa a rota (`montar_rota`) e a parada do `lures.json`. Na integração, o cursor deve seguir a rota do próprio player.
2. **Fuga automática** (sair pela saída vertical de `lures.json` quando o Ditto está fora e há monstro sem stun). Hoje só `[PERIGO]` + beep.
3. **Acelerar a leitura das caveiras** (~130 ms; 2,7 leituras/s com monstros): exige validar contra gravações dele. É o gargalo da cura (já saiu tarde) e do R.
4. **Monstros não localizados** (lista 13 a 16 contra 10 a 12 localizados): o mergulho e a conta de alcance não os enxergam. Investigar oclusão/corte no topo (ampliar a busca para cima não mudou nada em 25/10).
5. **Fase 6:** pokébola opcional (espaço) e humanização do conjunto.

**Pendências técnicas menores:** (a) o teleporte às vezes não é visto (assumido em 1,2 s): ver se vale ajustar `TELEPORTE_MAX_S`; (b) o G às vezes não confirma logo após um revive; (c) o `--ditto` ainda não foi validado com a rota gravada em curvas no fim do lure (o sentido do lure sai dos últimos pontos); (d) `R_MIN_MONSTROS` = 3 pode bloquear lures com 1 a 2 Gogoats; (e) aviso `mss.mss` deprecated (trocar por `mss.MSS`); (f) opcional: salvar print automático em `[R] NÃO apertado`, `[PERIGO]` e `AJUSTE`.

**Pergunta aberta ao usuário:** quer que o bot dê preferência a alguma estratégia (mergulho ou retirada) em lures específicos?

## 9. Como começar o chat novo

**Anexe:** todos os `.py` da pasta (lista da seção 3, **incluindo `bot_fase1.py`, `ditto_ctl.py`, `alvo_ditto.py`, `monitor_combate.py`, `detector_stun.py`, `barra_skills.py`**), `layout.json`, `config_gravador.json`, `config_player.json`, `config_bot.json`, `mapa_obstaculos.json`, `mapa_recortado.png`, o `lures.json` mais recente (pasta `gravacoes`), o **log e um print recentes** do `bot_fase1` (`monitor_logs`) e este documento + o guia de uso.

**Mensagem inicial sugerida:** *Vou continuar um projeto em andamento. Leia o documento de passagem e o guia de uso e os arquivos anexos. Fases 1 a 4 (G/F/C, R, revive e Ditto com ajuste) estão prontas e utilizáveis. Quero começar a fase 5 (integrar tudo ao player_lures.py). Atalhos do bot: F=f, G=g, C=c, R=r, revive=e, mover=o, parar=p. Parada de emergência: Shift+F. Vou testar direto em um lure, acompanhando. Responda curto, em português, e valide antes de afirmar.*

**Para manter a memória:** ao fim de cada etapa peça *atualize o documento de passagem*.
