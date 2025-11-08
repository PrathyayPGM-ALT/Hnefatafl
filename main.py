# hnefatafl_multiplayer.py
import pygame
import sys
import socket
import threading
import json
import queue

# ------------------ WINDOW / PYGAME ------------------
WIDTH = 900
HEIGHT = 900

pygame.init()
screen = pygame.display.set_mode((WIDTH, HEIGHT))
clock = pygame.time.Clock()
pygame.display.set_caption("Hnefatafl, NOT FALAFEL")

# ------------------ COLORS ------------------
BLACK = (0, 0, 0)
WHITE = (255, 255, 255)
GREEN = (0, 128, 0)
RED = (255, 0, 0)
GOLD = (255, 215, 0)
BROWN = (139, 69, 19)

# ------------------ GAME CONST ------------------
BOARD_SIZE = 9
CELL_SIZE = WIDTH // BOARD_SIZE
KING = 0
DEFENDER = 1
ATTACKER = 2

# ------------------ NETWORK CONFIG ------------------
SERVER_HOST = "100.76.152.128" 
SERVER_PORT = 8765

# =====================================================
#                       NETWORK
# =====================================================
class NetClient:
    def __init__(self, host, port, room_code, nickname):
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.sock.connect((host, port))
        self.sock_lock = threading.Lock()
        self.inbox = queue.Queue()
        self.alive = True

        # Join room with name
        self.send_json({"type": "join", "room": str(room_code), "name": nickname})

        # Listener thread
        self.t = threading.Thread(target=self._recv_loop, daemon=True)
        self.t.start()

    def send_json(self, obj):
        data = (json.dumps(obj) + "\n").encode("utf-8")
        with self.sock_lock:
            try:
                self.sock.sendall(data)
            except Exception:
                pass

    def _recv_loop(self):
        buff = b""
        while self.alive:
            try:
                chunk = self.sock.recv(4096)
                if not chunk:
                    break
                buff += chunk
                while b"\n" in buff:
                    line, buff = buff.split(b"\n", 1)
                    try:
                        obj = json.loads(line.decode("utf-8").strip())
                        self.inbox.put(obj)
                    except Exception:
                        pass
            except Exception:
                break
        self.alive = False

    def close(self):
        self.alive = False
        try:
            self.sock.close()
        except Exception:
            pass

# =====================================================
#                       GAME LOGIC
# =====================================================
class Hnefatafl:
    def __init__(self):
        self.board = [[None for _ in range(BOARD_SIZE)] for _ in range(BOARD_SIZE)]
        self.selected_piece = None
        self.current_player = DEFENDER
        self.game_over = False
        self.winner = None
        self.setup_board()

        # Multiplayer fields
        self.my_side = None        # "DEFENDER" or "ATTACKER"
        self.turn_side = None      # "DEFENDER" or "ATTACKER"
        self.my_name = None
        self.opponent_name = None
        self.waiting = True        # lobby/wait state
        self.net = None

    def setup_board(self):
        for row in range(BOARD_SIZE):
            for col in range(BOARD_SIZE):
                self.board[row][col] = None

        center = BOARD_SIZE // 2
        self.board[center][center] = KING

        # defenders around king
        self.board[center-1][center] = DEFENDER
        self.board[center+1][center] = DEFENDER
        self.board[center][center-1] = DEFENDER
        self.board[center][center+1] = DEFENDER
        self.board[center-1][center-1] = DEFENDER
        self.board[center-1][center+1] = DEFENDER
        self.board[center+1][center-1] = DEFENDER
        self.board[center+1][center+1] = DEFENDER

        # attackers at edge centers, plus +/-1 in orthogonal directions
        edge_positions = [
            (0, center), (BOARD_SIZE-1, center),
            (center, 0), (center, BOARD_SIZE-1)
        ]
        for row, col in edge_positions:
            offsets = [(-1, 0), (1, 0), (0, -1), (0, 1)]
            for dr, dc in offsets:
                new_row, new_col = row + dr, col + dc
                if 0 <= new_row < BOARD_SIZE and 0 <= new_col < BOARD_SIZE:
                    if self.board[new_row][new_col] is None:
                        self.board[new_row][new_col] = ATTACKER

    def is_castle(self, row, col):
        return row == BOARD_SIZE // 2 and col == BOARD_SIZE // 2

    def is_throne(self, row, col):
        center = BOARD_SIZE // 2
        return (row == center and col == center) or \
               (row == center-1 and col == center) or \
               (row == center+1 and col == center) or \
               (row == center and col == center-1) or \
               (row == center and col == center+1)

    def is_edge(self, row, col):
        return row == 0 or row == BOARD_SIZE-1 or col == 0 or col == BOARD_SIZE-1

    def is_corner(self, row, col):
        return (row, col) in [(0,0), (0, BOARD_SIZE-1), (BOARD_SIZE-1, 0), (BOARD_SIZE-1, BOARD_SIZE-1)]

    def get_valid_moves(self, row, col):
        if self.board[row][col] is None:
            return []
        piece_type = self.board[row][col]
        valid_moves = []
        directions = [(-1, 0), (1, 0), (0, -1), (0, 1)]
        for dr, dc in directions:
            for distance in range(1, BOARD_SIZE):
                new_row, new_col = row + dr * distance, col + dc * distance
                if not (0 <= new_row < BOARD_SIZE and 0 <= new_col < BOARD_SIZE):
                    break
                # attackers can't move into corners (reserved)
                if piece_type == ATTACKER and self.is_corner(new_row, new_col):
                    continue
                # blocked by piece
                if self.board[new_row][new_col] is not None:
                    # note: your original "king can move to empty castle" branch
                    # never triggers because the board[new] check prevents it.
                    # leaving as-is to keep logic identical.
                    break
                # king can move onto edges (escape)
                if piece_type == KING and self.is_edge(new_row, new_col):
                    valid_moves.append((new_row, new_col))
                    continue
                valid_moves.append((new_row, new_col))
        return valid_moves

    def move_piece(self, from_row, from_col, to_row, to_col, send=True):
        if (to_row, to_col) not in self.get_valid_moves(from_row, from_col):
            return False

        piece_type = self.board[from_row][from_col]
        self.board[from_row][from_col] = None
        self.board[to_row][to_col] = piece_type

        self.check_captures(to_row, to_col)
        self.check_win_conditions()

        # Toggle numeric current_player for legacy UI compatibility
        self.current_player = DEFENDER if self.current_player == ATTACKER else ATTACKER

        # Toggle network side turn tracker
        if self.turn_side:
            self.turn_side = "DEFENDER" if self.turn_side == "ATTACKER" else "ATTACKER"

        if send and self.net:
            self.net.send_json({"type": "move",
                                "from": [from_row, from_col],
                                "to": [to_row, to_col]})
        return True

    def check_captures(self, row, col):
        moving_piece = self.board[row][col]
        directions = [(-1, 0), (1, 0), (0, -1), (0, 1)]
        for dr, dc in directions:
            target_row, target_col = row + dr, col + dc
            if not (0 <= target_row < BOARD_SIZE and 0 <= target_col < BOARD_SIZE):
                continue
            target_piece = self.board[target_row][target_col]
            if target_piece is None or target_piece == moving_piece:
                continue
            opposite_row, opposite_col = target_row + dr, target_col + dc
            if not (0 <= opposite_row < BOARD_SIZE and 0 <= opposite_col < BOARD_SIZE):
                continue
            opposite_piece = self.board[opposite_row][opposite_col]
            if target_piece == KING:
                self.check_king_capture(target_row, target_col)
            elif opposite_piece == moving_piece:
                self.board[target_row][target_col] = None

    def check_king_capture(self, king_row, king_col):
        # In-castle: 4 attackers
        if self.is_castle(king_row, king_col):
            directions = [(-1, 0), (1, 0), (0, -1), (0, 1)]
            attackers_count = 0
            for dr, dc in directions:
                adj_row, adj_col = king_row + dr, king_col + dc
                if (0 <= adj_row < BOARD_SIZE and 0 <= adj_col < BOARD_SIZE and 
                    self.board[adj_row][adj_col] == ATTACKER):
                    attackers_count += 1
            if attackers_count == 4:
                self.board[king_row][king_col] = None
                self.game_over = True
                self.winner = ATTACKER
        # Adjacent to castle: 3 attackers (castle counts if empty)
        elif self.is_throne(king_row, king_col):
            directions = [(-1, 0), (1, 0), (0, -1), (0, 1)]
            attackers_count = 0
            for dr, dc in directions:
                adj_row, adj_col = king_row + dr, king_col + dc
                if (0 <= adj_row < BOARD_SIZE and 0 <= adj_col < BOARD_SIZE):
                    if self.board[adj_row][adj_col] == ATTACKER:
                        attackers_count += 1
                    elif self.is_castle(adj_row, adj_col):
                        attackers_count += 1
            if attackers_count >= 3:
                self.board[king_row][king_col] = None
                self.game_over = True
                self.winner = ATTACKER
        # Else: sandwiched by two attackers on opposite sides
        else:
            directions = [(-1, 0), (1, 0), (0, -1), (0, 1)]
            for dr, dc in directions:
                adj_row, adj_col = king_row + dr, king_col + dc
                opp_row, opp_col = king_row - dr, king_col - dc
                if (0 <= adj_row < BOARD_SIZE and 0 <= adj_col < BOARD_SIZE and
                    0 <= opp_row < BOARD_SIZE and 0 <= opp_col < BOARD_SIZE):
                    if (self.board[adj_row][adj_col] == ATTACKER and 
                        self.board[opp_row][opp_col] == ATTACKER):
                        self.board[king_row][king_col] = None
                        self.game_over = True
                        self.winner = ATTACKER
                        break

    def check_win_conditions(self):
        # King escapes to an edge
        for row in range(BOARD_SIZE):
            for col in range(BOARD_SIZE):
                if self.board[row][col] == KING and self.is_edge(row, col):
                    self.game_over = True
                    self.winner = DEFENDER
                    return
        # King captured (handled elsewhere) -> confirm king presence
        king_exists = any(self.board[row][col] == KING 
                          for row in range(BOARD_SIZE) for col in range(BOARD_SIZE))
        if not king_exists:
            self.game_over = True
            self.winner = ATTACKER

# =====================================================
#                   RENDERING / UI
# =====================================================
def draw_board(game, status_msg=None):
    screen.fill(BROWN)

    # grid
    for row in range(BOARD_SIZE + 1):
        pygame.draw.line(screen, BLACK, (0, row * CELL_SIZE), (WIDTH, row * CELL_SIZE), 2)
        pygame.draw.line(screen, BLACK, (row * CELL_SIZE, 0), (row * CELL_SIZE, HEIGHT), 2)

    # castle + throne
    center = BOARD_SIZE // 2
    castle_rect = pygame.Rect(center * CELL_SIZE, center * CELL_SIZE, CELL_SIZE, CELL_SIZE)
    pygame.draw.rect(screen, (200, 200, 200), castle_rect)

    throne_positions = [(center-1, center), (center+1, center), (center, center-1), (center, center+1)]
    for r, c in throne_positions:
        throne_rect = pygame.Rect(c * CELL_SIZE, r * CELL_SIZE, CELL_SIZE, CELL_SIZE)
        pygame.draw.rect(screen, (220, 220, 220), throne_rect)

    # pieces
    for row in range(BOARD_SIZE):
        for col in range(BOARD_SIZE):
            piece = game.board[row][col]
            cx = col * CELL_SIZE + CELL_SIZE // 2
            cy = row * CELL_SIZE + CELL_SIZE // 2
            radius = CELL_SIZE // 3
            if piece == KING:
                pygame.draw.circle(screen, GOLD, (cx, cy), radius)
                pygame.draw.circle(screen, BLACK, (cx, cy), radius, 2)
            elif piece == DEFENDER:
                pygame.draw.circle(screen, WHITE, (cx, cy), radius)
                pygame.draw.circle(screen, BLACK, (cx, cy), radius, 2)
            elif piece == ATTACKER:
                pygame.draw.circle(screen, RED, (cx, cy), radius)
                pygame.draw.circle(screen, BLACK, (cx, cy), radius, 2)

    # selected & valid moves
    if game.selected_piece:
        row, col = game.selected_piece
        highlight_rect = pygame.Rect(col * CELL_SIZE, row * CELL_SIZE, CELL_SIZE, CELL_SIZE)
        pygame.draw.rect(screen, (255, 255, 0), highlight_rect, 3)
        for mr, mc in game.get_valid_moves(row, col):
            move_rect = pygame.Rect(mc * CELL_SIZE, mr * CELL_SIZE, CELL_SIZE, CELL_SIZE)
            pygame.draw.rect(screen, (0, 255, 0), move_rect, 2)

    # status line
    font = pygame.font.Font(None, 36)
    line = None
    color = GREEN
    if game.game_over:
        winner_text = "Defenders Win!" if game.winner == DEFENDER else "Attackers Win!"
        line = winner_text
        color = RED
    else:
        if game.waiting:
            who = f" ({game.my_name})" if game.my_name else ""
            opp = f" vs {game.opponent_name}" if game.opponent_name else ""
            line = f"Waiting for opponent{who}{opp}..."
        else:
            turn = f"Turn: {game.turn_side}"
            mine = f"You are {game.my_side}"
            vs = f" vs {game.opponent_name}" if game.opponent_name else ""
            line = f"{turn} | {mine}{vs}"

    if status_msg:
        line = (line + " | " + status_msg) if line else status_msg

    if line:
        text = font.render(line, True, color)
        screen.blit(text, (WIDTH // 2 - text.get_width() // 2, 20))

def text_input_screen(prompt, digits_only=False, max_len=12):
    font = pygame.font.Font(None, 48)
    input_str = ""
    while True:
        screen.fill((30,30,30))
        txt = font.render(prompt, True, WHITE)
        screen.blit(txt, (50, 100))
        box = pygame.Rect(50, 180, 800, 60)
        pygame.draw.rect(screen, WHITE, box, 2)
        val = font.render(input_str, True, WHITE)
        screen.blit(val, (60, 190))
        hint = pygame.font.Font(None, 28).render("Enter to confirm, Esc to quit, Backspace to edit", True, (200,200,200))
        screen.blit(hint, (50, 260))
        pygame.display.flip()

        for event in pygame.event.get():
            if event.type == pygame.QUIT:
                pygame.quit(); sys.exit()
            if event.type == pygame.KEYDOWN:
                if event.key == pygame.K_ESCAPE:
                    pygame.quit(); sys.exit()
                elif event.key == pygame.K_RETURN:
                    return input_str.strip()
                elif event.key == pygame.K_BACKSPACE:
                    input_str = input_str[:-1]
                else:
                    ch = event.unicode
                    if ch:
                        if len(input_str) < max_len:
                            if digits_only:
                                if ch.isdigit():
                                    input_str += ch
                            else:
                                # allow simple alnum + underscore/hyphen/space
                                if ch.isalnum() or ch in "_- ":
                                    input_str += ch

# =====================================================
#                       MAIN LOOP
# =====================================================
def main():
    # 1) Ask for room code and nickname
    room_code = text_input_screen("Enter room code (numbers only):", digits_only=True, max_len=12)
    nickname = text_input_screen("Enter your nickname:", digits_only=False, max_len=16)

    game = Hnefatafl()
    game.my_name = nickname

    # 2) Connect to relay
    status_msg = "Connecting..."
    try:
        net = NetClient(SERVER_HOST, SERVER_PORT, room_code, nickname)
        game.net = net
    except Exception:
        status_msg = "Failed to connect"
        draw_board(game, status_msg); pygame.display.flip()
        pygame.time.wait(2000)
        pygame.quit(); sys.exit()

    # 3) Main loop
    while True:
        # Handle inbound network messages
        try:
            while True:
                msg = game.net.inbox.get_nowait()
                mtype = msg.get("type")
                if mtype == "waiting":
                    players = msg.get("players", [])
                    status_msg = "Waiting for opponent... (" + ", ".join(players) + ")"
                    game.waiting = True
                elif mtype == "joined":
                    jn = msg.get("name","Someone")
                    status_msg = f"{jn} joined. Waiting for opponent..."
                    game.waiting = True
                elif mtype == "start":
                    game.my_side = msg.get("your_side")
                    game.turn_side = msg.get("current_player")
                    game.current_player = ATTACKER if game.turn_side == "ATTACKER" else DEFENDER
                    game.opponent_name = msg.get("opponent_name","Opponent")
                    status_msg = f"You are {game.my_side}. Opponent: {game.opponent_name}"
                    game.waiting = False
                elif mtype == "move":
                    fr = msg.get("from", [0,0]); to = msg.get("to", [0,0])
                    game.move_piece(fr[0], fr[1], to[0], to[1], send=False)
                elif mtype == "opponent_left":
                    left_name = msg.get("name","Opponent")
                    status_msg = f"{left_name} left. Waiting for opponent..."
                    game.waiting = True
                    game.opponent_name = None
                elif mtype == "error":
                    status_msg = f"Error: {msg.get('msg','')}"
                elif mtype == "full":
                    status_msg = "Room is full"
        except queue.Empty:
            pass

        # Handle local events
        for event in pygame.event.get():
            if event.type == pygame.QUIT:
                if game.net: game.net.close()
                pygame.quit(); sys.exit()

            if not game.game_over and not game.waiting and event.type == pygame.MOUSEBUTTONDOWN:
                # Only allow clicking when it's my turn
                my_turn = (
                    (game.my_side == "DEFENDER" and game.turn_side == "DEFENDER") or
                    (game.my_side == "ATTACKER" and game.turn_side == "ATTACKER")
                )
                if not my_turn:
                    continue

                col = event.pos[0] // CELL_SIZE
                row = event.pos[1] // CELL_SIZE
                if 0 <= row < BOARD_SIZE and 0 <= col < BOARD_SIZE:
                    if game.selected_piece:
                        from_row, from_col = game.selected_piece
                        # Enforce I can only move my pieces (incl. KING if I’m DEFENDER)
                        piece = game.board[from_row][from_col]
                        mine = (game.my_side == "DEFENDER" and (piece == DEFENDER or piece == KING)) or \
                               (game.my_side == "ATTACKER" and piece == ATTACKER)
                        if mine and game.move_piece(from_row, from_col, row, col, send=True):
                            game.selected_piece = None
                        else:
                            piece2 = game.board[row][col]
                            mine2 = (game.my_side == "DEFENDER" and (piece2 == DEFENDER or piece2 == KING)) or \
                                    (game.my_side == "ATTACKER" and piece2 == ATTACKER)
                            game.selected_piece = (row, col) if piece2 is not None and mine2 else None
                    else:
                        piece = game.board[row][col]
                        mine = (game.my_side == "DEFENDER" and (piece == DEFENDER or piece == KING)) or \
                               (game.my_side == "ATTACKER" and piece == ATTACKER)
                        game.selected_piece = (row, col) if piece is not None and mine else None

        draw_board(game, status_msg)
        pygame.display.flip()
        clock.tick(60)

if __name__ == "__main__":
    main()
