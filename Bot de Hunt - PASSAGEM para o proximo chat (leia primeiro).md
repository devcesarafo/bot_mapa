# Bot de Hunt – PASSAGEM para o próximo chat (leia este primeiro)

*Atualizado em 17/10/2026. Resumo curto do estado do projeto. O histórico completo e cronológico está no documento anterior, «Bot de Hunt – Especificação para continuidade» (consultar só se precisar de um detalhe).*

## 1. Como trabalhar com este usuário

- Fala **português (BR)**. Respostas diretas e curtas; quer **gastar poucos créditos**.
- Testa jogando e manda **prints, JSON e logs**. Valoriza **dados medidos** e honestidade sobre **limites** ('não testei no jogo real').
- Prefere avançar **em etapas pequenas**, cada uma testada antes da próxima. Aceita ajustes pequenos e específicos.
- **Nunca afirmar sem validar** nas gravações dele. Ao fim de cada etapa: atualizar este documento.

## 2. Objetivo e regra de ouro

Bot em Python (Windows) para o jogo **PokeAlliance** (janela 1920×1080; o jogo roda como **administrador**, então o terminal também). Ele percorre uma hunt em laço de **lures**: puxa Gogoats até um ponto de parada, o **Ditto** (pokémon dele) os mata com a macro **R**, revive, e repete.

**Regra de ouro: o treinador nunca pode morrer** (um golpe de Gogoat mata). Falha de leitura **nunca** pode parecer 'seguro' (mostrar cinza, não verde). Automatizar jogo pode violar termos de uso; o risco é do usuário.

## 3. Arquivos prontos (todos em Python)

| Arquivo | Para quê | Estado |
| --- | --- | --- |
| `leitor_coord.py` | Lê a coordenada `X, Y, Z` do minimapa (tabela de dígitos, 0,12 ms) | Pronto, validado |
| `editor_mapa.py` | Editor do mapa de obstáculos (`mapa_obstaculos.json`, tile 8 px, âncora tile (30,17) = jogo (684,1303,7)); classe `ObstacleMap` | Pronto; mapa já feito |
| `bot_mapa.py` | Localizador por imagem do minimapa no `mapa_recortado.png` (escala 2.0) | Pronto, hoje só referência |
| `gravador_lures.py` | Grava o trajeto andado em `lures.json` (segmentos WASD, paradas, saída vertical); Ctrl+Alt+G/P | Testado no jogo |
| `player_lures.py` | Reproduz o laço (A\* + WASD + destravar + humanização 0 a 2); modos simulação, dry-run e real | **Andou o laço no jogo real** |
| `envio_teclas.py`, `teste_teclas.py` | Envio de teclas (SendInput) e diagnóstico | OK como administrador |
| `atalhos.py` | Captura de atalhos (teclas e botões do mouse `mouse:x1`, `mouse:x2`) e `Vigia` | Testado |
| `gravador_quadros.py` | Calibra o layout (`layout.json`) e grava a tela a 10 fps + eventos; `separar_amostra.py` monta zips leves para enviar | Usado nas gravações |
| `battle_list.py` | Lê a Battle list (Ditto presente e vida, monstros, shiny pelo ícone); `calibrar()` acha o painel sozinho (o HUD muda) | Validado |
| `detector_stun.py` | Anéis de stun, caveiras, stun **monstro a monstro**, `achar_ditto`, área do Whirlwind | Validado em replay; útil ao vivo |
| `barra_skills.py` | Cooldown de F, G, C e F2 pelo brilho do ícone; revive = barra preta | Validado |
| `monitor_combate.py` | **Dry-run**: 4 cartões (ATAQUE, DEFESA, CURA, REVIVE), janela 194×560 sempre no topo, Print (Ctrl+Alt+S), registra os apertos do usuário, modo `--replay` | **Usado e aprovado pelo usuário** |

O bot **ainda não aperta teclas de combate**: só anda (player) e só recomenda (monitor).

## 4. Fatos do jogo (medidos)

- Tile ≈ 72,3 px; visão 21×11 tiles (10 de cada lado, 5 acima e abaixo); personagem no centro.
- Treinador: passo reto 316 ms (\~3,2 tiles/s), diagonal 617 ms (= dois retos). Gogoat: 2 tiles/s, só 4 direções.
- **Stun (Whirlwind, F2):** área 7×7 sem os cantos (3 tiles para cada lado), \~6,2 s, cooldown 40 s, é a 3ª skill do macro R. Sinal visual: **anel branco que PISCA a \~5 Hz** em cada monstro stunado.
- **R:** macro do jogo; o usuário aperta em rajada (6 a 9 vezes) por precaução; 1 aperto já bastou em testes. **C logo antes do R** atrasa (0,6 a 0,8 s) e enfraquece o macro.
- **Skills:** F defesa 12 s (CD 40), G velocidade 10 s (CD 40, usar cedo), C cura (CD 100, usar com a vida do Ditto ≤ 45%), revive \~1 s reseta tudo; o Ditto some da Battle list 0,4 a 0,8 s no revive.
- Quem para o Ditto: **parar** e **mover** (leva o Ditto ao cursor). Depois de mover ele volta ao estado livre e segue o treinador, por isso o usuário repete o mover \~4,5×/s durante o lure, com o cursor \~3 a 4 tiles à frente.
- Tempos: o 1º R vem **2,7 a 5,0 s depois de o personagem parar** (mediana 4,1 s); lure \~20 s; laço completo \~100 s.
- Atalhos do usuário: revive `e`, R `r`, parar `p` ou `mouse:x1`, mover `o` ou `mouse:x2`, pokébola espaço, F defesa, G velocidade, C cura, F2 stun. O bot **precisa de atalhos de teclado próprios** para mover e parar (o usuário configura um segundo atalho no jogo).
- O **HUD muda** com frequência. Fixos hoje: coordenada (1730,54,120×14), barra de skills (x: F 747, G 782, C 957, F2 992; y 952 a 992), área do jogo (200,93)-(1720,887).

## 5. Regras de combate já implementadas no monitor (constantes no topo do arquivo)

- **ATAQUE (R):** só com o personagem parado há ≥ 2,5 s, nº de monstros estável por 1 s e **todos dentro da área do Whirlwind** (usa a posição do Ditto); espera máxima 5 s; emergência (Ditto ≤ 55% com 3+ monstros, ou lure ≥ 20 s) manda R mas **avisa quantos ficam fora do alcance**; se há monstro fora, sugere mover o Ditto na direção dele.
- **DEFESA:** G cedo (assim que há monstro sem stun), depois F quando o Ditto perde 3% ou aos 8 s de lure; só se houver monstro sem stun; avisa 'Falta G' ou 'Falta F'.
- **CURA:** C com a vida ≤ 45%; avisa para esperar \~1 s antes do R.
- **REVIVE:** vermelho 'fugir' se o Ditto está fora e algum monstro está sem stun; 'não reviver' se N de M sem stun; 'liberado' com todos stunados e ≥ 2,5 s de stun; 'resetar skills' quando não há monstros.
- **Stun com TRAVA:** quando todos são confirmados, vale 6,2 s e só cai com evidência contrária (monstro se mexeu, monstro novo, Ditto levando dano).

## 6. Erros já cometidos e corrigidos (não repetir)

1. **Contar anéis com tolerância (80%)** deixou um Gogoat sem stun passar. Hoje é **monstro a monstro** (caveira + anel).
2. O anel **pisca**: precisa de ≥ 8 leituras/s e janela de 0,5 s. Ao analisar amostras de 1 quadro/s, a ausência do anel **não prova nada**.
3. O **redemoinho azul NÃO é o stun** (é efeito de 'capturável' no corpo; some quando a pokébola é lançada).
4. O tempo entre parar e o R se mede pela **coordenada de todos os quadros**, não pelo fim dos cliques de 'mover'.
5. Lista da Battle list vazia por não achar o painel mostrava **verde**; agora mostra **cinza** com aviso.
6. Achar o Ditto pelo texto ou pela cor do rótulo **não funciona** (a cor muda com a vida; rótulos de Gogoats cheios também são verdes). Funciona pela **barra de vida sem caveira**, com largura = 26 × vida.
7. Teclas que não chegam ao jogo: era o jogo como **administrador**; rodar o terminal como administrador.

## 7. Próximo passo (combinado, ainda não feito)

O usuário aprovou o monitor e quer o bot **apertando** as teclas. Fases, da menos arriscada à mais:

1. **Defesa e cura automáticas** (G, F, C), confirmando pelo cooldown na barra de skills e repetindo se não saiu.
2. **R em rajada** nos momentos que o monitor recomenda (primeiro em dry-run).
3. **Revive com a regra de segurança** (só com todos stunados e stun sobrando; senão fugir pela saída vertical gravada em `lures.json`).
4. **Ditto:** mover (mouse até o ponto à frente + atalho de teclado, repetido, ritmo humanizado) e parar na parada.
5. **Integrar tudo ao `player_lures.py`** (andar, mover o Ditto, parar, atacar, reviver, andar).
6. Pokébola opcional (espaço, na posição do cursor, no shiny e opcionalmente nos comuns, até andando) e humanização do conjunto.

**Perguntas pendentes ao usuário (responder antes da fase 1):** (a) quais teclas o **bot** usa para F, G, C, R, revive, mover e parar; (b) qual tecla de **parada de emergência** (hoje Ctrl+Alt+X); (c) testar num lugar seguro sem monstros ou direto num lure com ele acompanhando.

**Outras pendências técnicas:** o Ditto só é achado em 35% a 65% dos quadros (usa memória de 1,5 s); posição fixa da barra de skills; caso 'R falhou em stunar com shiny vivo' nunca gravado; fuga de emergência ainda não implementada no bot; ícones de outros pokémons não cadastrados em `battle_list.py`.

## 8. Como começar o chat novo

**Anexe:** todos os `.py` da pasta (lista acima), `mapa_recortado.png`, `mapa_obstaculos.json`, `layout.json`, `config_gravador.json`, `config_player.json` (se existir), o `lures.json` mais recente (pasta `gravacoes`) e, se tiver, um print e JSON recentes do monitor (`monitor_logs/prints`).

**Mensagem inicial sugerida:** *Vou continuar um projeto já em andamento. Leia o documento de passagem em anexo (ou no link) e os arquivos anexos. Quero começar a fase 1 (defesa e cura automáticas). Meus atalhos do bot são: \[preencher\]. Responda curto, em português, e valide antes de afirmar.*

**Para manter a memória:** ao fim de cada etapa peça *atualize o documento de passagem*. Se o chat novo ficar lento de novo, repita este processo.

## 9. Decisões do usuário (17/10/2026): valem para a fase 1

As três perguntas pendentes da seção 7 foram **respondidas**:

- **Atalhos do bot (teclado):** defesa F = `f`; velocidade G = `g`; cura C = `c`; R = `r`; revive = `e`; mover o Ditto = `o`; parar o Ditto = `p`. (O jogo aceita vários atalhos por função; o usuário usa os botões do mouse `x1`/`x2` para mover e parar, e estas teclas são as do bot.)
- **Parada de emergência: `Shift+F`.** Ao implementar: (1) soltar **todas** as teclas e parar tudo na hora; (2) o `player_lures.py` hoje usa `Ctrl+Alt+X`, então **unificar** nos dois (ou aceitar as duas); (3) **cuidado:** `F` é a tecla da defesa. Se o jogo tratar `Shift+F` como `F`, apertar a parada de emergência também usaria a defesa (inofensivo, mas o bot deve ignorar o `f` que chega junto com o Shift); confirmar com o usuário que `Shift+F` não está associado a nada no jogo.
- **Como testar:** **direto em um lure real, com o usuário acompanhando.** Por isso a fase 1 (G, F e C automáticos) deve ter: log visível de cada tecla enviada e o motivo, confirmação pelo cooldown na barra de skills, repetição limitada (no máximo 2 tentativas por skill), e a parada de emergência sempre ativa. Essas três teclas não podem matar o personagem, o que torna a fase 1 segura para testar assim.

**Mensagem inicial pronta para o chat novo:** *Vou continuar um projeto em andamento. Leia o documento de passagem e os arquivos anexos. Quero começar a fase 1 (defesa e cura automáticas). Atalhos do bot: F=f, G=g, C=c, R=r, revive=e, mover=o, parar=p. Parada de emergência: Shift+F. Vou testar direto em um lure, acompanhando. Responda curto, em português, e valide antes de afirmar.*
