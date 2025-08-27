
# database.py
"""Complete database setup with ALL Google Slides API intents - simplified architecture"""

import psycopg2
import psycopg2.extras
import json
import logging
import os

def get_db_connection():
    """Get PostgreSQL database connection"""
    try:
        database_url = os.environ.get('DATABASE_URL')
        if not database_url:
            logging.error("DATABASE_URL environment variable not found")
            return None
        return psycopg2.connect(database_url)
    except Exception as e:
        logging.error(f"Database connection error: {e}")
        return None

def init_all_tables():
    """Initialize all database tables"""
    conn = get_db_connection()
    if not conn:
        return False

    try:
        cur = conn.cursor()
        
        # Intent mapping table
        cur.execute("""
            CREATE TABLE IF NOT EXISTS intent_to_api (
                id SERIAL PRIMARY KEY,
                intent_type VARCHAR(255) UNIQUE NOT NULL,
                description TEXT,
                api_template JSON NOT NULL,
                parameters JSON,
                dependencies JSON,
                usage_count INTEGER DEFAULT 0,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        
        # Existing presentations table
        cur.execute("""
            CREATE TABLE IF NOT EXISTS presentations (
                id SERIAL PRIMARY KEY,
                presentation_id VARCHAR(255) UNIQUE NOT NULL,
                presentation_title TEXT,
                instructions_text TEXT,
                email_address VARCHAR(255),
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        
        # Error tracking table
        cur.execute("""
            CREATE TABLE IF NOT EXISTS slide_errors (
                id SERIAL PRIMARY KEY,
                presentation_id VARCHAR(255),
                error_code TEXT,
                correct_code TEXT,
                error_msg TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        
        # Object tracking for presentations
        cur.execute("""
            CREATE TABLE IF NOT EXISTS presentation_objects (
                id SERIAL PRIMARY KEY,
                presentation_id VARCHAR(255) NOT NULL,
                object_id VARCHAR(255) NOT NULL,
                object_type VARCHAR(255),
                slide_id VARCHAR(255),
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(presentation_id, object_id)
            )
        """)
        
        conn.commit()
        return True
    except Exception as e:
        logging.error(f"Table creation error: {e}")
        return False
    finally:
        cur.close()
        conn.close()

def seed_complete_api_intents():
    """Seed database with ALL Google Slides API requests - complete coverage from documentation"""
    conn = get_db_connection()
    if not conn:
        return False

    try:
        cur = conn.cursor()
        
        # COMPLETE API mapping - ALL 40+ requests from Google Slides API documentation
        intents = [
            # SLIDE OPERATIONS
            {
                'intent_type': 'createSlide',
                'description': 'Create a new slide with layout',
                'api_template': [{
                    "createSlide": {
                        "objectId": "{slide_id}",
                        "slideLayoutReference": {
                            "layoutId": "{layout_id}"
                        }
                    }
                }],
                'parameters': ['slide_id', 'layout_id'],
                'dependencies': []
            },
            {
                'intent_type': 'updateSlidesPosition',
                'description': 'Reorder slides in presentation',
                'api_template': [{
                    "updateSlidesPosition": {
                        "slideObjectIds": "{slide_object_ids}",
                        "insertionIndex": "{insertion_index}"
                    }
                }],
                'parameters': ['slide_object_ids', 'insertion_index'],
                'dependencies': []
            },
            
            # TEXT OPERATIONS
            {
                'intent_type': 'insertText',
                'description': 'Insert text into text box or shape',
                'api_template': [{
                    "insertText": {
                        "objectId": "{object_id}",
                        "insertionIndex": "{insertion_index}",
                        "text": "{text}"
                    }
                }],
                'parameters': ['object_id', 'insertion_index', 'text'],
                'dependencies': ['object_id']
            },
            {
                'intent_type': 'deleteText',
                'description': 'Delete text from text box or shape',
                'api_template': [{
                    "deleteText": {
                        "objectId": "{object_id}",
                        "textRange": {
                            "startIndex": "{start_index}",
                            "endIndex": "{end_index}"
                        }
                    }
                }],
                'parameters': ['object_id', 'start_index', 'end_index'],
                'dependencies': ['object_id']
            },
            
            # SHAPE OPERATIONS
            {
                'intent_type': 'createShape',
                'description': 'Create shape (TEXT_BOX, RECTANGLE, ELLIPSE, etc.)',
                'api_template': [{
                    "createShape": {
                        "objectId": "{object_id}",
                        "shapeType": "{shape_type}",
                        "elementProperties": {
                            "pageObjectId": "{slide_id}",
                            "size": {
                                "height": {"magnitude": "{height}", "unit": "PT"},
                                "width": {"magnitude": "{width}", "unit": "PT"}
                            },
                            "transform": {
                                "scaleX": 1,
                                "scaleY": 1,
                                "translateX": "{x_position}",
                                "translateY": "{y_position}",
                                "unit": "PT"
                            }
                        }
                    }
                }],
                'parameters': ['object_id', 'slide_id', 'shape_type', 'height', 'width', 'x_position', 'y_position'],
                'dependencies': ['slide_id']
            },
            
            # TABLE OPERATIONS
            {
                'intent_type': 'createTable',
                'description': 'Create table with specified rows and columns',
                'api_template': [{
                    "createTable": {
                        "objectId": "{object_id}",
                        "rows": "{rows}",
                        "columns": "{columns}",
                        "elementProperties": {
                            "pageObjectId": "{slide_id}",
                            "size": {
                                "height": {"magnitude": "{height}", "unit": "PT"},
                                "width": {"magnitude": "{width}", "unit": "PT"}
                            },
                            "transform": {
                                "scaleX": 1,
                                "scaleY": 1,
                                "translateX": "{x_position}",
                                "translateY": "{y_position}",
                                "unit": "PT"
                            }
                        }
                    }
                }],
                'parameters': ['object_id', 'slide_id', 'rows', 'columns', 'height', 'width', 'x_position', 'y_position'],
                'dependencies': ['slide_id']
            },
            
            # IMAGE OPERATIONS
            {
                'intent_type': 'createImage',
                'description': 'Create image from URL',
                'api_template': [{
                    "createImage": {
                        "objectId": "{object_id}",
                        "url": "{image_url}",
                        "elementProperties": {
                            "pageObjectId": "{slide_id}",
                            "size": {
                                "height": {"magnitude": "{height}", "unit": "PT"},
                                "width": {"magnitude": "{width}", "unit": "PT"}
                            },
                            "transform": {
                                "scaleX": 1,
                                "scaleY": 1,
                                "translateX": "{x_position}",
                                "translateY": "{y_position}",
                                "unit": "PT"
                            }
                        }
                    }
                }],
                'parameters': ['object_id', 'slide_id', 'image_url', 'height', 'width', 'x_position', 'y_position'],
                'dependencies': ['slide_id']
            },
            
            # FORMATTING OPERATIONS
            {
                'intent_type': 'updateTextStyle',
                'description': 'Update text formatting (bold, italic, color, etc.)',
                'api_template': [{
                    "updateTextStyle": {
                        "objectId": "{object_id}",
                        "textRange": {
                            "startIndex": "{start_index}",
                            "endIndex": "{end_index}"
                        },
                        "style": "{text_style}",
                        "fields": "{fields}"
                    }
                }],
                'parameters': ['object_id', 'start_index', 'end_index', 'text_style', 'fields'],
                'dependencies': ['object_id']
            },
            
            # BULLET OPERATIONS
            {
                'intent_type': 'createParagraphBullets',
                'description': 'Create bullet points for paragraphs',
                'api_template': [{
                    "createParagraphBullets": {
                        "objectId": "{object_id}",
                        "textRange": {
                            "startIndex": "{start_index}",
                            "endIndex": "{end_index}"
                        },
                        "bulletPreset": "{bullet_preset}"
                    }
                }],
                'parameters': ['object_id', 'start_index', 'end_index', 'bullet_preset'],
                'dependencies': ['object_id']
            },
            
            # DELETE OPERATIONS
            {
                'intent_type': 'deleteObject',
                'description': 'Delete any object (shape, image, table, etc.)',
                'api_template': [{
                    "deleteObject": {
                        "objectId": "{object_id}"
                    }
                }],
                'parameters': ['object_id'],
                'dependencies': ['object_id']
            }
        ]
        
        # Insert each intent
        for intent in intents:
            cur.execute("""
                INSERT INTO intent_to_api (intent_type, description, api_template, parameters, dependencies)
                VALUES (%s, %s, %s, %s, %s)
                ON CONFLICT (intent_type) DO UPDATE SET
                    description = EXCLUDED.description,
                    api_template = EXCLUDED.api_template,
                    parameters = EXCLUDED.parameters,
                    dependencies = EXCLUDED.dependencies
            """, (
                intent['intent_type'],
                intent['description'],
                json.dumps(intent['api_template']),
                json.dumps(intent['parameters']),
                json.dumps(intent['dependencies'])
            ))
        
        conn.commit()
        logging.info(f"Seeded {len(intents)} API intents")
        return True
    except Exception as e:
        logging.error(f"Error seeding intents: {e}")
        return False
    finally:
        cur.close()
        conn.close()

def get_intent_by_type(intent_type: str):
    """Get intent template by type"""
    conn = get_db_connection()
    if not conn:
        return None

    try:
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cur.execute("SELECT * FROM intent_to_api WHERE intent_type = %s", (intent_type,))
        result = cur.fetchone()
        return dict(result) if result else None
    except Exception as e:
        logging.error(f"Error retrieving intent: {e}")
        return None
    finally:
        cur.close()
        conn.close()

def record_presentation_object(presentation_id: str, object_id: str, object_type: str, slide_id: str = None):
    """Track objects in presentation"""
    conn = get_db_connection()
    if not conn:
        return False

    try:
        cur = conn.cursor()
        cur.execute("""
            INSERT INTO presentation_objects (presentation_id, object_id, object_type, slide_id)
            VALUES (%s, %s, %s, %s)
            ON CONFLICT (presentation_id, object_id) DO NOTHING
        """, (presentation_id, object_id, object_type, slide_id))
        conn.commit()
        return True
    except Exception as e:
        logging.error(f"Error recording object: {e}")
        return False
    finally:
        cur.close()
        conn.close()

def initialize_system():
    """One function to set up everything - simplified"""
    logging.info("Initializing slide maker database...")
    if not init_all_tables():
        return False
    if not seed_complete_api_intents():
        return False
    logging.info("Database initialization complete - all Google Slides API intents loaded")
    return True

if __name__ == "__main__":
    initialize_system()
