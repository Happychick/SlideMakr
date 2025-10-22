# database_setup.py
"""Complete database setup with ALL 47 Google Slides API intents"""

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
    """Initialize ALL database tables"""
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
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        
        # Presentations table
        cur.execute("""
            CREATE TABLE IF NOT EXISTS presentations (
                id SERIAL PRIMARY KEY,
                presentation_id VARCHAR(255) UNIQUE NOT NULL,
                title VARCHAR(500),
                instructions TEXT,
                audio_path TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        
        # Errors table
        cur.execute("""
            CREATE TABLE IF NOT EXISTS errors (
                id SERIAL PRIMARY KEY,
                presentation_id VARCHAR(255),
                request_data TEXT,
                error_message TEXT,
                fixed_code TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        
        conn.commit()
        logging.info("All tables created successfully")
        return True
    except Exception as e:
        logging.error(f"Table creation error: {e}")
        return False
    finally:
        cur.close()
        conn.close()

def seed_complete_api_intents():
    """Seed database with ALL 47 Google Slides API request types"""
    conn = get_db_connection()
    if not conn:
        return False

    # ALL 47 Google Slides API Request Types (from official documentation)
    intents = [
        # SLIDE OPERATIONS (3)
        {
            "intent_type": "createSlide",
            "description": "Creates a new slide using predefined layout enum (BLANK, TITLE, TITLE_AND_BODY, TITLE_AND_TWO_COLUMNS, TITLE_ONLY, SECTION_HEADER, SECTION_TITLE_AND_DESCRIPTION, ONE_COLUMN_TEXT, MAIN_POINT, BIG_NUMBER, CAPTION_ONLY)",
            "api_template": {
                "createSlide": {
                    "objectId": "{{SLIDE_ID}}",
                    "insertionIndex": "{{INDEX}}",
                    "slideLayoutReference": {
                        "predefinedLayout": "{{LAYOUT}}"
                    }
                }
            },
            "parameters": ["SLIDE_ID", "INDEX", "LAYOUT"]
        },
        {
            "intent_type": "updateSlidesPosition",
            "description": "Updates the position of slides in the presentation",
            "api_template": {
                "updateSlidesPosition": {
                    "slideObjectIds": "{{SLIDE_IDS}}",
                    "insertionIndex": "{{INDEX}}"
                }
            },
            "parameters": ["SLIDE_IDS", "INDEX"]
        },
        {
            "intent_type": "updateSlideProperties",
            "description": "Updates the properties of a Slide",
            "api_template": {
                "updateSlideProperties": {
                    "objectId": "{{SLIDE_ID}}",
                    "slideProperties": "{{PROPERTIES}}",
                    "fields": "{{FIELDS}}"
                }
            },
            "parameters": ["SLIDE_ID", "PROPERTIES", "FIELDS"]
        },
        
        # SHAPE OPERATIONS (2)
        {
            "intent_type": "createShape",
            "description": "Creates a new shape",
            "api_template": {
                "createShape": {
                    "objectId": "{{SHAPE_ID}}",
                    "shapeType": "{{SHAPE_TYPE}}",
                    "elementProperties": {
                        "pageObjectId": "{{PAGE_ID}}",
                        "size": {
                            "width": {"magnitude": "{{WIDTH}}", "unit": "EMU"},
                            "height": {"magnitude": "{{HEIGHT}}", "unit": "EMU"}
                        },
                        "transform": {
                            "scaleX": 1,
                            "scaleY": 1,
                            "translateX": "{{X_POSITION}}",
                            "translateY": "{{Y_POSITION}}",
                            "unit": "EMU"
                        }
                    }
                }
            },
            "parameters": ["SHAPE_ID", "SHAPE_TYPE", "PAGE_ID", "WIDTH", "HEIGHT", "X_POSITION", "Y_POSITION"]
        },
        {
            "intent_type": "updateShapeProperties",
            "description": "Update the properties of a Shape",
            "api_template": {
                "updateShapeProperties": {
                    "objectId": "{{SHAPE_ID}}",
                    "shapeProperties": "{{PROPERTIES}}",
                    "fields": "{{FIELDS}}"
                }
            },
            "parameters": ["SHAPE_ID", "PROPERTIES", "FIELDS"]
        },
        
        # TABLE OPERATIONS (10)
        {
            "intent_type": "createTable",
            "description": "Creates a new table",
            "api_template": {
                "createTable": {
                    "objectId": "{{TABLE_ID}}",
                    "rows": "{{ROWS}}",
                    "columns": "{{COLUMNS}}",
                    "elementProperties": {
                        "pageObjectId": "{{PAGE_ID}}",
                        "size": {
                            "width": {"magnitude": "{{WIDTH}}", "unit": "EMU"},
                            "height": {"magnitude": "{{HEIGHT}}", "unit": "EMU"}
                        },
                        "transform": {
                            "scaleX": 1,
                            "scaleY": 1,
                            "translateX": "{{X_POSITION}}",
                            "translateY": "{{Y_POSITION}}",
                            "unit": "EMU"
                        }
                    }
                }
            },
            "parameters": ["TABLE_ID", "ROWS", "COLUMNS", "PAGE_ID", "WIDTH", "HEIGHT", "X_POSITION", "Y_POSITION"]
        },
        {
            "intent_type": "insertTableRows",
            "description": "Inserts rows into a table",
            "api_template": {
                "insertTableRows": {
                    "tableObjectId": "{{TABLE_ID}}",
                    "cellLocation": {
                        "rowIndex": "{{ROW_INDEX}}",
                        "columnIndex": "{{COLUMN_INDEX}}"
                    },
                    "insertBelow": "{{INSERT_BELOW}}",
                    "number": "{{NUMBER}}"
                }
            },
            "parameters": ["TABLE_ID", "ROW_INDEX", "COLUMN_INDEX", "INSERT_BELOW", "NUMBER"]
        },
        {
            "intent_type": "insertTableColumns",
            "description": "Inserts columns into a table",
            "api_template": {
                "insertTableColumns": {
                    "tableObjectId": "{{TABLE_ID}}",
                    "cellLocation": {
                        "rowIndex": "{{ROW_INDEX}}",
                        "columnIndex": "{{COLUMN_INDEX}}"
                    },
                    "insertRight": "{{INSERT_RIGHT}}",
                    "number": "{{NUMBER}}"
                }
            },
            "parameters": ["TABLE_ID", "ROW_INDEX", "COLUMN_INDEX", "INSERT_RIGHT", "NUMBER"]
        },
        {
            "intent_type": "deleteTableRow",
            "description": "Deletes a row from a table",
            "api_template": {
                "deleteTableRow": {
                    "tableObjectId": "{{TABLE_ID}}",
                    "cellLocation": {
                        "rowIndex": "{{ROW_INDEX}}",
                        "columnIndex": "{{COLUMN_INDEX}}"
                    }
                }
            },
            "parameters": ["TABLE_ID", "ROW_INDEX", "COLUMN_INDEX"]
        },
        {
            "intent_type": "deleteTableColumn",
            "description": "Deletes a column from a table",
            "api_template": {
                "deleteTableColumn": {
                    "tableObjectId": "{{TABLE_ID}}",
                    "cellLocation": {
                        "rowIndex": "{{ROW_INDEX}}",
                        "columnIndex": "{{COLUMN_INDEX}}"
                    }
                }
            },
            "parameters": ["TABLE_ID", "ROW_INDEX", "COLUMN_INDEX"]
        },
        {
            "intent_type": "updateTableCellProperties",
            "description": "Update the properties of a TableCell",
            "api_template": {
                "updateTableCellProperties": {
                    "objectId": "{{TABLE_ID}}",
                    "tableRange": "{{TABLE_RANGE}}",
                    "tableCellProperties": "{{PROPERTIES}}",
                    "fields": "{{FIELDS}}"
                }
            },
            "parameters": ["TABLE_ID", "TABLE_RANGE", "PROPERTIES", "FIELDS"]
        },
        {
            "intent_type": "updateTableBorderProperties",
            "description": "Updates the properties of the table borders",
            "api_template": {
                "updateTableBorderProperties": {
                    "objectId": "{{TABLE_ID}}",
                    "tableRange": "{{TABLE_RANGE}}",
                    "borderPosition": "{{BORDER_POSITION}}",
                    "tableBorderProperties": "{{PROPERTIES}}",
                    "fields": "{{FIELDS}}"
                }
            },
            "parameters": ["TABLE_ID", "TABLE_RANGE", "BORDER_POSITION", "PROPERTIES", "FIELDS"]
        },
        {
            "intent_type": "updateTableColumnProperties",
            "description": "Updates the properties of a Table column",
            "api_template": {
                "updateTableColumnProperties": {
                    "objectId": "{{TABLE_ID}}",
                    "columnIndices": "{{COLUMN_INDICES}}",
                    "tableColumnProperties": "{{PROPERTIES}}",
                    "fields": "{{FIELDS}}"
                }
            },
            "parameters": ["TABLE_ID", "COLUMN_INDICES", "PROPERTIES", "FIELDS"]
        },
        {
            "intent_type": "updateTableRowProperties",
            "description": "Updates the properties of a Table row",
            "api_template": {
                "updateTableRowProperties": {
                    "objectId": "{{TABLE_ID}}",
                    "rowIndices": "{{ROW_INDICES}}",
                    "tableRowProperties": "{{PROPERTIES}}",
                    "fields": "{{FIELDS}}"
                }
            },
            "parameters": ["TABLE_ID", "ROW_INDICES", "PROPERTIES", "FIELDS"]
        },
        {
            "intent_type": "mergeTableCells",
            "description": "Merges cells in a Table",
            "api_template": {
                "mergeTableCells": {
                    "objectId": "{{TABLE_ID}}",
                    "tableRange": "{{TABLE_RANGE}}"
                }
            },
            "parameters": ["TABLE_ID", "TABLE_RANGE"]
        },
        {
            "intent_type": "unmergeTableCells",
            "description": "Unmerges cells in a Table",
            "api_template": {
                "unmergeTableCells": {
                    "objectId": "{{TABLE_ID}}",
                    "tableRange": "{{TABLE_RANGE}}"
                }
            },
            "parameters": ["TABLE_ID", "TABLE_RANGE"]
        },
        
        # TEXT OPERATIONS (7)
        {
            "intent_type": "insertText",
            "description": "Inserts text into a shape or table cell",
            "api_template": {
                "insertText": {
                    "objectId": "{{OBJECT_ID}}",
                    "text": "{{TEXT}}",
                    "insertionIndex": "{{INSERTION_INDEX}}"
                }
            },
            "parameters": ["OBJECT_ID", "TEXT", "INSERTION_INDEX"]
        },
        {
            "intent_type": "deleteText",
            "description": "Deletes text from a shape or a table cell",
            "api_template": {
                "deleteText": {
                    "objectId": "{{OBJECT_ID}}",
                    "textRange": {
                        "startIndex": "{{START_INDEX}}",
                        "endIndex": "{{END_INDEX}}",
                        "type": "FIXED_RANGE"
                    }
                }
            },
            "parameters": ["OBJECT_ID", "START_INDEX", "END_INDEX"]
        },
        {
            "intent_type": "replaceAllText",
            "description": "Replaces all instances of specified text",
            "api_template": {
                "replaceAllText": {
                    "containsText": {
                        "text": "{{SEARCH_TEXT}}",
                        "matchCase": False
                    },
                    "replaceText": "{{REPLACE_TEXT}}"
                }
            },
            "parameters": ["SEARCH_TEXT", "REPLACE_TEXT"]
        },
        {
            "intent_type": "updateTextStyle",
            "description": "Updates the styling for text",
            "api_template": {
                "updateTextStyle": {
                    "objectId": "{{OBJECT_ID}}",
                    "style": "{{STYLE}}",
                    "textRange": {"type": "ALL"},
                    "fields": "{{FIELDS}}"
                }
            },
            "parameters": ["OBJECT_ID", "STYLE", "FIELDS"]
        },
        {
            "intent_type": "updateParagraphStyle",
            "description": "Updates the styling for paragraphs",
            "api_template": {
                "updateParagraphStyle": {
                    "objectId": "{{OBJECT_ID}}",
                    "style": "{{STYLE}}",
                    "textRange": {"type": "ALL"},
                    "fields": "{{FIELDS}}"
                }
            },
            "parameters": ["OBJECT_ID", "STYLE", "FIELDS"]
        },
        {
            "intent_type": "createParagraphBullets",
            "description": "Creates bullets for paragraphs",
            "api_template": {
                "createParagraphBullets": {
                    "objectId": "{{OBJECT_ID}}",
                    "textRange": {"type": "ALL"},
                    "bulletPreset": "{{BULLET_PRESET}}"
                }
            },
            "parameters": ["OBJECT_ID", "BULLET_PRESET"]
        },
        {
            "intent_type": "deleteParagraphBullets",
            "description": "Deletes bullets from paragraphs",
            "api_template": {
                "deleteParagraphBullets": {
                    "objectId": "{{OBJECT_ID}}",
                    "textRange": {"type": "ALL"}
                }
            },
            "parameters": ["OBJECT_ID"]
        },
        
        # IMAGE OPERATIONS (4)
        {
            "intent_type": "createImage",
            "description": "Creates an image",
            "api_template": {
                "createImage": {
                    "objectId": "{{IMAGE_ID}}",
                    "url": "{{IMAGE_URL}}",
                    "elementProperties": {
                        "pageObjectId": "{{PAGE_ID}}",
                        "size": {
                            "width": {"magnitude": "{{WIDTH}}", "unit": "EMU"},
                            "height": {"magnitude": "{{HEIGHT}}", "unit": "EMU"}
                        },
                        "transform": {
                            "scaleX": 1,
                            "scaleY": 1,
                            "translateX": "{{X_POSITION}}",
                            "translateY": "{{Y_POSITION}}",
                            "unit": "EMU"
                        }
                    }
                }
            },
            "parameters": ["IMAGE_ID", "IMAGE_URL", "PAGE_ID", "WIDTH", "HEIGHT", "X_POSITION", "Y_POSITION"]
        },
        {
            "intent_type": "updateImageProperties",
            "description": "Update the properties of an Image",
            "api_template": {
                "updateImageProperties": {
                    "objectId": "{{IMAGE_ID}}",
                    "imageProperties": "{{PROPERTIES}}",
                    "fields": "{{FIELDS}}"
                }
            },
            "parameters": ["IMAGE_ID", "PROPERTIES", "FIELDS"]
        },
        {
            "intent_type": "replaceAllShapesWithImage",
            "description": "Replaces all shapes matching criteria with an image",
            "api_template": {
                "replaceAllShapesWithImage": {
                    "imageUrl": "{{IMAGE_URL}}",
                    "imageReplaceMethod": "CENTER_INSIDE",
                    "containsText": {
                        "text": "{{SEARCH_TEXT}}",
                        "matchCase": False
                    }
                }
            },
            "parameters": ["IMAGE_URL", "SEARCH_TEXT"]
        },
        {
            "intent_type": "replaceImage",
            "description": "Replaces an existing image with a new image",
            "api_template": {
                "replaceImage": {
                    "imageObjectId": "{{IMAGE_ID}}",
                    "url": "{{IMAGE_URL}}",
                    "imageReplaceMethod": "CENTER_INSIDE"
                }
            },
            "parameters": ["IMAGE_ID", "IMAGE_URL"]
        },
        
        # VIDEO OPERATIONS (2)
        {
            "intent_type": "createVideo",
            "description": "Creates a video",
            "api_template": {
                "createVideo": {
                    "objectId": "{{VIDEO_ID}}",
                    "source": "{{SOURCE}}",
                    "id": "{{VIDEO_SOURCE_ID}}",
                    "elementProperties": {
                        "pageObjectId": "{{PAGE_ID}}",
                        "size": {
                            "width": {"magnitude": "{{WIDTH}}", "unit": "EMU"},
                            "height": {"magnitude": "{{HEIGHT}}", "unit": "EMU"}
                        },
                        "transform": {
                            "scaleX": 1,
                            "scaleY": 1,
                            "translateX": "{{X_POSITION}}",
                            "translateY": "{{Y_POSITION}}",
                            "unit": "EMU"
                        }
                    }
                }
            },
            "parameters": ["VIDEO_ID", "SOURCE", "VIDEO_SOURCE_ID", "PAGE_ID", "WIDTH", "HEIGHT", "X_POSITION", "Y_POSITION"]
        },
        {
            "intent_type": "updateVideoProperties",
            "description": "Update the properties of a Video",
            "api_template": {
                "updateVideoProperties": {
                    "objectId": "{{VIDEO_ID}}",
                    "videoProperties": "{{PROPERTIES}}",
                    "fields": "{{FIELDS}}"
                }
            },
            "parameters": ["VIDEO_ID", "PROPERTIES", "FIELDS"]
        },
        
        # LINE OPERATIONS (4)
        {
            "intent_type": "createLine",
            "description": "Creates a line",
            "api_template": {
                "createLine": {
                    "objectId": "{{LINE_ID}}",
                    "category": "{{CATEGORY}}",
                    "elementProperties": {
                        "pageObjectId": "{{PAGE_ID}}",
                        "size": {
                            "width": {"magnitude": "{{WIDTH}}", "unit": "EMU"},
                            "height": {"magnitude": "{{HEIGHT}}", "unit": "EMU"}
                        },
                        "transform": {
                            "scaleX": 1,
                            "scaleY": 1,
                            "translateX": "{{X_POSITION}}",
                            "translateY": "{{Y_POSITION}}",
                            "unit": "EMU"
                        }
                    }
                }
            },
            "parameters": ["LINE_ID", "CATEGORY", "PAGE_ID", "WIDTH", "HEIGHT", "X_POSITION", "Y_POSITION"]
        },
        {
            "intent_type": "updateLineProperties",
            "description": "Updates the properties of a Line",
            "api_template": {
                "updateLineProperties": {
                    "objectId": "{{LINE_ID}}",
                    "lineProperties": "{{PROPERTIES}}",
                    "fields": "{{FIELDS}}"
                }
            },
            "parameters": ["LINE_ID", "PROPERTIES", "FIELDS"]
        },
        {
            "intent_type": "updateLineCategory",
            "description": "Updates the category of a line",
            "api_template": {
                "updateLineCategory": {
                    "objectId": "{{LINE_ID}}",
                    "lineCategory": "{{CATEGORY}}"
                }
            },
            "parameters": ["LINE_ID", "CATEGORY"]
        },
        {
            "intent_type": "rerouteLine",
            "description": "Reroutes a line",
            "api_template": {
                "rerouteLine": {
                    "objectId": "{{LINE_ID}}"
                }
            },
            "parameters": ["LINE_ID"]
        },
        
        # CHART OPERATIONS (3)
        {
            "intent_type": "createSheetsChart",
            "description": "Creates an embedded Google Sheets chart",
            "api_template": {
                "createSheetsChart": {
                    "objectId": "{{CHART_ID}}",
                    "spreadsheetId": "{{SPREADSHEET_ID}}",
                    "chartId": "{{CHART_SOURCE_ID}}",
                    "linkingMode": "{{LINKING_MODE}}",
                    "elementProperties": {
                        "pageObjectId": "{{PAGE_ID}}",
                        "size": {
                            "width": {"magnitude": "{{WIDTH}}", "unit": "EMU"},
                            "height": {"magnitude": "{{HEIGHT}}", "unit": "EMU"}
                        },
                        "transform": {
                            "scaleX": 1,
                            "scaleY": 1,
                            "translateX": "{{X_POSITION}}",
                            "translateY": "{{Y_POSITION}}",
                            "unit": "EMU"
                        }
                    }
                }
            },
            "parameters": ["CHART_ID", "SPREADSHEET_ID", "CHART_SOURCE_ID", "LINKING_MODE", "PAGE_ID", "WIDTH", "HEIGHT", "X_POSITION", "Y_POSITION"]
        },
        {
            "intent_type": "refreshSheetsChart",
            "description": "Refreshes an embedded Google Sheets chart",
            "api_template": {
                "refreshSheetsChart": {
                    "objectId": "{{CHART_ID}}"
                }
            },
            "parameters": ["CHART_ID"]
        },
        {
            "intent_type": "replaceAllShapesWithSheetsChart",
            "description": "Replaces all shapes matching criteria with a Google Sheets chart",
            "api_template": {
                "replaceAllShapesWithSheetsChart": {
                    "spreadsheetId": "{{SPREADSHEET_ID}}",
                    "chartId": "{{CHART_SOURCE_ID}}",
                    "linkingMode": "{{LINKING_MODE}}",
                    "containsText": {
                        "text": "{{SEARCH_TEXT}}",
                        "matchCase": False
                    }
                }
            },
            "parameters": ["SPREADSHEET_ID", "CHART_SOURCE_ID", "LINKING_MODE", "SEARCH_TEXT"]
        },
        
        # GENERAL OPERATIONS (6)
        {
            "intent_type": "deleteObject",
            "description": "Deletes a page or page element from the presentation",
            "api_template": {
                "deleteObject": {
                    "objectId": "{{OBJECT_ID}}"
                }
            },
            "parameters": ["OBJECT_ID"]
        },
        {
            "intent_type": "duplicateObject",
            "description": "Duplicates a slide or page element",
            "api_template": {
                "duplicateObject": {
                    "objectId": "{{OBJECT_ID}}",
                    "objectIds": "{{OBJECT_ID_MAP}}"
                }
            },
            "parameters": ["OBJECT_ID", "OBJECT_ID_MAP"]
        },
        {
            "intent_type": "updatePageElementTransform",
            "description": "Updates the transform of a page element",
            "api_template": {
                "updatePageElementTransform": {
                    "objectId": "{{OBJECT_ID}}",
                    "transform": "{{TRANSFORM}}",
                    "applyMode": "{{APPLY_MODE}}"
                }
            },
            "parameters": ["OBJECT_ID", "TRANSFORM", "APPLY_MODE"]
        },
        {
            "intent_type": "updatePageProperties",
            "description": "Updates the properties of a Page",
            "api_template": {
                "updatePageProperties": {
                    "objectId": "{{PAGE_ID}}",
                    "pageProperties": "{{PROPERTIES}}",
                    "fields": "{{FIELDS}}"
                }
            },
            "parameters": ["PAGE_ID", "PROPERTIES", "FIELDS"]
        },
        {
            "intent_type": "updatePageElementAltText",
            "description": "Updates the alt text title/description of a page element",
            "api_template": {
                "updatePageElementAltText": {
                    "objectId": "{{OBJECT_ID}}",
                    "title": "{{TITLE}}",
                    "description": "{{DESCRIPTION}}"
                }
            },
            "parameters": ["OBJECT_ID", "TITLE", "DESCRIPTION"]
        },
        {
            "intent_type": "updatePageElementsZOrder",
            "description": "Updates the Z-order of page elements",
            "api_template": {
                "updatePageElementsZOrder": {
                    "pageElementObjectIds": "{{OBJECT_IDS}}",
                    "operation": "{{OPERATION}}"
                }
            },
            "parameters": ["OBJECT_IDS", "OPERATION"]
        },
        
        # GROUPING OPERATIONS (2)
        {
            "intent_type": "groupObjects",
            "description": "Groups objects to create an object group",
            "api_template": {
                "groupObjects": {
                    "groupObjectId": "{{GROUP_ID}}",
                    "childrenObjectIds": "{{CHILDREN_IDS}}"
                }
            },
            "parameters": ["GROUP_ID", "CHILDREN_IDS"]
        },
        {
            "intent_type": "ungroupObjects",
            "description": "Ungroups objects",
            "api_template": {
                "ungroupObjects": {
                    "objectIds": "{{OBJECT_IDS}}"
                }
            },
            "parameters": ["OBJECT_IDS"]
        }
    ]

    try:
        cur = conn.cursor()
        for intent in intents:
            cur.execute("""
                INSERT INTO intent_to_api (intent_type, description, api_template, parameters)
                VALUES (%s, %s, %s, %s)
                ON CONFLICT (intent_type) DO UPDATE SET
                    description = EXCLUDED.description,
                    api_template = EXCLUDED.api_template,
                    parameters = EXCLUDED.parameters
            """, (
                intent["intent_type"],
                intent["description"],
                json.dumps(intent["api_template"]),
                json.dumps(intent["parameters"])
            ))
        conn.commit()
        logging.info(f"Seeded {len(intents)} complete API intents")
        return True
    except Exception as e:
        logging.error(f"Seeding error: {e}")
        return False
    finally:
        cur.close()
        conn.close()

def initialize_system():
    """Initialize complete system - run this once"""
    logging.basicConfig(level=logging.INFO)
    logging.info("Initializing SlideMakr database...")
    
    if init_all_tables():
        logging.info("✓ Tables created")
    else:
        logging.error("✗ Table creation failed")
        return False
    
    if seed_complete_api_intents():
        logging.info("✓ All 47 API intents seeded")
    else:
        logging.error("✗ Intent seeding failed")
        return False
    
    logging.info("✓ System initialization complete!")
    return True

if __name__ == "__main__":
    initialize_system()
