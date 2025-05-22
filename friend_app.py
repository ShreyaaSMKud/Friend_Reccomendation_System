# friend_app.py

import sqlite3
import logging
from typing import List, Dict, Set
from datetime import datetime
import networkx as nx

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

class DatabaseManager:
    def __init__(self, db_path='friend_recommendations.db'):
        self.db_path = db_path
        self.connection = None

    def connect(self):
        try:
            self.connection = sqlite3.connect(self.db_path)
            self.connection.row_factory = sqlite3.Row
            logger.info(f"Connected to database: {self.db_path}")
            return True
        except sqlite3.Error as e:
            logger.error(f"Database connection error: {e}")
            return False

    def create_tables(self):
        if not self.connection:
            return False

        cursor = self.connection.cursor()
        try:
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS users (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT NOT NULL,
                    email TEXT UNIQUE NOT NULL,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)

            cursor.execute("""
                CREATE TABLE IF NOT EXISTS interests (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER,
                    interest TEXT NOT NULL,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE,
                    UNIQUE(user_id, interest)
                )
            """)

            cursor.execute("""
                CREATE TABLE IF NOT EXISTS friends (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER,
                    friend_id INTEGER,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE,
                    FOREIGN KEY (friend_id) REFERENCES users(id) ON DELETE CASCADE,
                    UNIQUE(user_id, friend_id)
                )
            """)

            cursor.execute("CREATE INDEX IF NOT EXISTS idx_interests_user_id ON interests(user_id)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_friends_user_id ON friends(user_id)")

            self.connection.commit()
            return True
        except sqlite3.Error as e:
            logger.error(f"Table creation error: {e}")
            return False
        finally:
            cursor.close()

    def close(self):
        if self.connection:
            self.connection.close()
            logger.info("Database closed")

class User:
    def __init__(self, db: DatabaseManager):
        self.db = db

    def create_user(self, name: str, email: str, interests: List[str] = None, friend_ids: List[int] = None):
        cursor = self.db.connection.cursor()
        try:
            cursor.execute("INSERT INTO users (name, email) VALUES (?, ?)", (name, email))
            user_id = cursor.lastrowid

            if interests:
                for interest in interests:
                    try:
                        cursor.execute("INSERT INTO interests (user_id, interest) VALUES (?, ?)", (user_id, interest.strip().lower()))
                    except sqlite3.IntegrityError:
                        continue

            if friend_ids:
                for fid in friend_ids:
                    cursor.execute("SELECT id FROM users WHERE id = ?", (fid,))
                    if cursor.fetchone():
                        try:
                            cursor.execute("INSERT INTO friends (user_id, friend_id) VALUES (?, ?)", (user_id, fid))
                            cursor.execute("INSERT INTO friends (user_id, friend_id) VALUES (?, ?)", (fid, user_id))
                        except sqlite3.IntegrityError:
                            continue

            self.db.connection.commit()
            return user_id
        except sqlite3.Error as e:
            self.db.connection.rollback()
            logger.error(f"User creation failed: {e}")
            return None
        finally:
            cursor.close()

    def get_user(self, user_id: int):
        cursor = self.db.connection.cursor()
        try:
            cursor.execute("SELECT * FROM users WHERE id = ?", (user_id,))
            row = cursor.fetchone()
            return dict(row) if row else None
        finally:
            cursor.close()

    def get_user_by_email(self, email: str):
        cursor = self.db.connection.cursor()
        try:
            cursor.execute("SELECT * FROM users WHERE email = ?", (email,))
            row = cursor.fetchone()
            return dict(row) if row else None
        finally:
            cursor.close()

    def get_all_users(self):
        cursor = self.db.connection.cursor()
        try:
            cursor.execute("SELECT * FROM users")
            return [dict(row) for row in cursor.fetchall()]
        finally:
            cursor.close()

    def get_user_interests(self, user_id: int) -> Set[str]:
        cursor = self.db.connection.cursor()
        try:
            cursor.execute("SELECT interest FROM interests WHERE user_id = ?", (user_id,))
            return {row['interest'] for row in cursor.fetchall()}
        finally:
            cursor.close()

class RecommendationEngine:
    def __init__(self, db_manager: DatabaseManager):
        self.db = db_manager
        self.user_manager = User(db_manager)
        self.graph = nx.Graph()
        self._build_graph()

    def _build_graph(self):
        users = self.user_manager.get_all_users()
        for user in users:
            self.graph.add_node(user['id'], name=user['name'], email=user['email'])

        cursor = self.db.connection.cursor()
        cursor.execute("SELECT user_id, friend_id FROM friends")
        for row in cursor.fetchall():
            self.graph.add_edge(row['user_id'], row['friend_id'])
        cursor.close()

    def jaccard_similarity(self, a: Set, b: Set) -> float:
        return len(a & b) / len(a | b) if a or b else 0.0

    def calculate_user_similarity(self, user1_id: int, user2_id: int) -> Dict:
        interests1 = self.user_manager.get_user_interests(user1_id)
        interests2 = self.user_manager.get_user_interests(user2_id)
        friends1 = set(self.graph.neighbors(user1_id))
        friends2 = set(self.graph.neighbors(user2_id))

        interest_sim = self.jaccard_similarity(interests1, interests2)
        mutual_sim = self.jaccard_similarity(friends1, friends2)
        combined = 0.6 * mutual_sim + 0.4 * interest_sim

        return {
            'interest_similarity': interest_sim,
            'mutual_friends_similarity': mutual_sim,
            'combined_score': combined,
            'common_interests': list(interests1 & interests2),
            'mutual_friends_count': len(friends1 & friends2)
        }

    def get_friend_recommendations(self, user_id: int, limit: int = 5) -> List[Dict]:
        current_friends = set(self.graph.neighbors(user_id))
        candidates = set(self.graph.nodes) - current_friends - {user_id}
        recommendations = []

        for cid in candidates:
            sim = self.calculate_user_similarity(user_id, cid)
            if sim['combined_score'] > 0:
                user = self.user_manager.get_user(cid)
                recommendations.append({
                    'user_id': cid,
                    'name': user['name'],
                    'email': user['email'],
                    'similarity_score': sim['combined_score'],
                    'common_interests': sim['common_interests'],
                    'mutual_friends_count': sim['mutual_friends_count'],
                    'interest_similarity': sim['interest_similarity'],
                    'mutual_friends_similarity': sim['mutual_friends_similarity']
                })

        return sorted(recommendations, key=lambda r: r['similarity_score'], reverse=True)[:limit]

class FriendRecommendationApp:
    def __init__(self):
        self.db = DatabaseManager()
        self.user_manager = None
        self.recommendation_engine = None
        self.current_user_id = None

    def initialize(self):
        if not self.db.connect():
            return False
        if not self.db.create_tables():
            return False
        self.user_manager = User(self.db)
        self.recommendation_engine = RecommendationEngine(self.db)
        return True

    def display_menu(self):
        print("\n" + "="*100)
        print("PEOPLE YOU MAY KNOW - FRIEND RECOMMENDATION SYSTEM".center(100))
        print("="*100)
        print("1. Create User")
        print("2. Login as User")
        print("3. View My Profile")
        print("4. Get Friend Recommendations")
        print("5. View All Users")
        print("0. Exit")
        print("-"*50)

    def run(self):
        if not self.initialize():
            print("Initialization failed.")
            return

        print("Welcome to the Friend Recommendation System!")
        while True:
            self.display_menu()
            choice = input("\nEnter your choice (0-5): ").strip()

            if choice == '1':
                name = input("Enter name: ")
                email = input("Enter email: ")
                interests = input("Enter interests (comma-separated): ").split(',')
                uid = self.user_manager.create_user(name, email, interests)
                print(f"User created with ID: {uid}")
            elif choice == '2':
                uid = int(input("Enter User ID: "))
                if self.user_manager.get_user(uid):
                    self.current_user_id = uid
                    print("Logged in.")
                else:
                    print("User not found.")
            elif choice == '3':
                if not self.current_user_id:
                    print("Please login first.")
                    continue
                user = self.user_manager.get_user(self.current_user_id)
                interests = self.user_manager.get_user_interests(self.current_user_id)
                print(f"\n--- Profile: {user['name']} ---")
                print(f"User ID: {user['id']}")
                print(f"Email: {user['email']}")
                print(f"Interests: {', '.join(interests)}")
            elif choice == '4':
                if not self.current_user_id:
                    print("Please login first.")
                    continue
                recs = self.recommendation_engine.get_friend_recommendations(self.current_user_id)
                for r in recs:
                    print(f"{r['name']} ({r['email']}) - Score: {r['similarity_score']:.2f}, Mutuals: {r['mutual_friends_count']}, Interests: {', '.join(r['common_interests'])}")
            elif choice == '5':
                users = self.user_manager.get_all_users()
                print("\n--- All Users ---")
                for user in users:
                    print(f"ID: {user['id']} | Name: {user['name']} | Email: {user['email']}")
            elif choice == '0':
                print("Goodbye!")
                break
            else:
                print("Invalid option.")

        self.db.close()

if __name__ == '__main__':
    app = FriendRecommendationApp()
    app.run()
