
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
    """Initialize ALL database tables in one place"""
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
                object_type VARCHAR(100) NOT NULL,
                slide_id VARCHAR(255),
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(presentation_id, object_id)
            )
        """)
        
        conn.commit()
        logging.info("All database tables initialized")
        return True
    except Exception as e:
        logging.error(f"Database initialization error: {e}")
        return False
    finally:
        cur.close()
        conn.close()

def seed_complete_api_intents():
    """Seed database with ALL Google Slides API requests - complete coverage from documentation"""
    conn = get_db_connection()
    if not conn:
        return False

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
        {
            'intent_type': 'updateSlideProperties',
            'description': 'Update slide properties',
            'api_template': [{
                "updateSlideProperties": {
                    "objectId": "{slide_id}",
                    "slideProperties": "{slide_properties}",
                    "fields": "{fields}"
                }
            }],
            'parameters': ['slide_id', 'slide_properties', 'fields'],
            'dependencies': ['slide_id']
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
        {
            'intent_type': 'updateShapeProperties',
            'description': 'Update shape properties (fill, outline, etc.)',
            'api_template': [{
                "updateShapeProperties": {
                    "objectId": "{object_id}",
                    "shapeProperties": "{shape_properties}",
                    "fields": "{fields}"
                }
            }],
            'parameters': ['object_id', 'shape_properties', 'fields'],
            'dependencies': ['object_id']
        },
        
        # TEXT OPERATIONS
        {
            'intent_type': 'insertText',
            'description': 'Insert text into shape or table cell',
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
            'intent_type': 'insertTextIntoTableCell',
            'description': 'Insert text into table cell at specific row/column. For inserting text into a table, we need to add the cellLocation information which is why this is included as a separate intent.',
            'api_template': [{
                "insertText": {
                    "objectId": "{object_id}",
                    "cellLocation": {
                        "rowIndex": "{row_index}",
                        "columnIndex": "{column_index}"
                    },
                    "insertionIndex": "{insertion_index}",
                    "text": "{text}"
                }
            }],
            'parameters': ['object_id', 'row_index', 'column_index', 'insertion_index', 'text'],
            'dependencies': ['object_id']
        },
        {
            'intent_type': 'deleteText',
            'description': 'Delete text from shape or table cell',
            'api_template': [{
                "deleteText": {
                    "objectId": "{object_id}",
                    "textRange": {
                        "type": "{range_type}"
                    }
                }
            }],
            'parameters': ['object_id', 'range_type'],
            'dependencies': ['object_id']
        },
        {
            'intent_type': 'replaceAllText',
            'description': 'Replace all instances of text',
            'api_template': [{
                "replaceAllText": {
                    "replaceText": "{replace_text}",
                    "containsText": {
                        "text": "{search_text}",
                        "matchCase": "{match_case}"
                    }
                }
            }],
            'parameters': ['replace_text', 'search_text', 'match_case'],
            'dependencies': []
        },
        {
            'intent_type': 'updateTextStyle',
            'description': 'Update text formatting (bold, italic, color, etc.)',
            'api_template': [{
                "updateTextStyle": {
                    "objectId": "{object_id}",
                    "textRange": {
                        "type": "{range_type}"
                    },
                    "style": "{text_style}",
                    "fields": "{fields}"
                }
            }],
            'parameters': ['object_id', 'range_type', 'text_style', 'fields'],
            'dependencies': ['object_id']
        },
        {
            'intent_type': 'updateParagraphStyle',
            'description': 'Update paragraph formatting (alignment, spacing, etc.)',
            'api_template': [{
                "updateParagraphStyle": {
                    "objectId": "{object_id}",
                    "textRange": {
                        "type": "{range_type}"
                    },
                    "style": "{paragraph_style}",
                    "fields": "{fields}"
                }
            }],
            'parameters': ['object_id', 'range_type', 'paragraph_style', 'fields'],
            'dependencies': ['object_id']
        },
        
        # BULLET OPERATIONS
        {
            'intent_type': 'createParagraphBullets',
            'description': 'Add bullet points to text',
            'api_template': [{
                "createParagraphBullets": {
                    "objectId": "{object_id}",
                    "textRange": {
                        "type": "{range_type}"
                    },
                    "bulletPreset": "{bullet_preset}"
                }
            }],
            'parameters': ['object_id', 'range_type', 'bullet_preset'],
            'dependencies': ['object_id']
        },
        {
            'intent_type': 'deleteParagraphBullets',
            'description': 'Remove bullet points from text',
            'api_template': [{
                "deleteParagraphBullets": {
                    "objectId": "{object_id}",
                    "textRange": {
                        "type": "{range_type}"
                    }
                }
            }],
            'parameters': ['object_id', 'range_type'],
            'dependencies': ['object_id']
        },
        
        # TABLE OPERATIONS
        {
            'intent_type': 'createTable',
            'description': 'Create table with rows and columns',
            'api_template': [{
                "createTable": {
                    "objectId": "{object_id}",
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
                    },
                    "rows": "{rows}",
                    "columns": "{columns}"
                }
            }],
            'parameters': ['object_id', 'slide_id', 'rows', 'columns', 'height', 'width', 'x_position', 'y_position'],
            'dependencies': ['slide_id']
        },
        {
            'intent_type': 'insertTableRows',
            'description': 'Insert rows into table',
            'api_template': [{
                "insertTableRows": {
                    "tableObjectId": "{table_object_id}",
                    "cellLocation": {
                        "rowIndex": "{row_index}",
                        "columnIndex": "{column_index}"
                    },
                    "insertBelow": "{insert_below}",
                    "number": "{number}"
                }
            }],
            'parameters': ['table_object_id', 'row_index', 'column_index', 'insert_below', 'number'],
            'dependencies': ['table_object_id']
        },
        {
            'intent_type': 'insertTableColumns',
            'description': 'Insert columns into table',
            'api_template': [{
                "insertTableColumns": {
                    "tableObjectId": "{table_object_id}",
                    "cellLocation": {
                        "rowIndex": "{row_index}",
                        "columnIndex": "{column_index}"
                    },
                    "insertRight": "{insert_right}",
                    "number": "{number}"
                }
            }],
            'parameters': ['table_object_id', 'row_index', 'column_index', 'insert_right', 'number'],
            'dependencies': ['table_object_id']
        },
        {
            'intent_type': 'deleteTableRow',
            'description': 'Delete row from table',
            'api_template': [{
                "deleteTableRow": {
                    "tableObjectId": "{table_object_id}",
                    "cellLocation": {
                        "rowIndex": "{row_index}",
                        "columnIndex": "{column_index}"
                    }
                }
            }],
            'parameters': ['table_object_id', 'row_index', 'column_index'],
            'dependencies': ['table_object_id']
        },
        {
            'intent_type': 'deleteTableColumn',
            'description': 'Delete column from table',
            'api_template': [{
                "deleteTableColumn": {
                    "tableObjectId": "{table_object_id}",
                    "cellLocation": {
                        "rowIndex": "{row_index}",
                        "columnIndex": "{column_index}"
                    }
                }
            }],
            'parameters': ['table_object_id', 'row_index', 'column_index'],
            'dependencies': ['table_object_id']
        },
        {
            'intent_type': 'updateTableCellProperties',
            'description': 'Update table cell properties',
            'api_template': [{
                "updateTableCellProperties": {
                    "objectId": "{table_object_id}",
                    "tableRange": {
                        "location": {
                            "rowIndex": "{row_index}",
                            "columnIndex": "{column_index}"
                        },
                        "rowSpan": "{row_span}",
                        "columnSpan": "{column_span}"
                    },
                    "tableCellProperties": "{cell_properties}",
                    "fields": "{fields}"
                }
            }],
            'parameters': ['table_object_id', 'row_index', 'column_index', 'row_span', 'column_span', 'cell_properties', 'fields'],
            'dependencies': ['table_object_id']
        },
        {
            'intent_type': 'updateTableBorderProperties',
            'description': 'Update table border properties',
            'api_template': [{
                "updateTableBorderProperties": {
                    "objectId": "{table_object_id}",
                    "tableRange": {
                        "location": {
                            "rowIndex": "{row_index}",
                            "columnIndex": "{column_index}"
                        },
                        "rowSpan": "{row_span}",
                        "columnSpan": "{column_span}"
                    },
                    "borderPosition": "{border_position}",
                    "tableBorderProperties": "{border_properties}",
                    "fields": "{fields}"
                }
            }],
            'parameters': ['table_object_id', 'row_index', 'column_index', 'row_span', 'column_span', 'border_position', 'border_properties', 'fields'],
            'dependencies': ['table_object_id']
        },
        {
            'intent_type': 'updateTableColumnProperties',
            'description': 'Update table column properties',
            'api_template': [{
                "updateTableColumnProperties": {
                    "objectId": "{table_object_id}",
                    "columnIndices": "{column_indices}",
                    "tableColumnProperties": "{column_properties}",
                    "fields": "{fields}"
                }
            }],
            'parameters': ['table_object_id', 'column_indices', 'column_properties', 'fields'],
            'dependencies': ['table_object_id']
        },
        {
            'intent_type': 'updateTableRowProperties',
            'description': 'Update table row properties',
            'api_template': [{
                "updateTableRowProperties": {
                    "objectId": "{table_object_id}",
                    "rowIndices": "{row_indices}",
                    "tableRowProperties": "{row_properties}",
                    "fields": "{fields}"
                }
            }],
            'parameters': ['table_object_id', 'row_indices', 'row_properties', 'fields'],
            'dependencies': ['table_object_id']
        },
        {
            'intent_type': 'mergeTableCells',
            'description': 'Merge cells in table',
            'api_template': [{
                "mergeTableCells": {
                    "objectId": "{table_object_id}",
                    "tableRange": {
                        "location": {
                            "rowIndex": "{row_index}",
                            "columnIndex": "{column_index}"
                        },
                        "rowSpan": "{row_span}",
                        "columnSpan": "{column_span}"
                    }
                }
            }],
            'parameters': ['table_object_id', 'row_index', 'column_index', 'row_span', 'column_span'],
            'dependencies': ['table_object_id']
        },
        {
            'intent_type': 'unmergeTableCells',
            'description': 'Unmerge cells in table',
            'api_template': [{
                "unmergeTableCells": {
                    "objectId": "{table_object_id}",
                    "tableRange": {
                        "location": {
                            "rowIndex": "{row_index}",
                            "columnIndex": "{column_index}"
                        },
                        "rowSpan": "{row_span}",
                        "columnSpan": "{column_span}"
                    }
                }
            }],
            'parameters': ['table_object_id', 'row_index', 'column_index', 'row_span', 'column_span'],
            'dependencies': ['table_object_id']
        },
        
        # IMAGE OPERATIONS
        {
            'intent_type': 'createImage',
            'description': 'Create image from URL',
            'api_template': [{
                "createImage": {
                    "objectId": "{object_id}",
                    "url": "{url}",
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
            'parameters': ['object_id', 'slide_id', 'url', 'height', 'width', 'x_position', 'y_position'],
            'dependencies': ['slide_id']
        },
        {
            'intent_type': 'updateImageProperties',
            'description': 'Update image properties (brightness, contrast, etc.)',
            'api_template': [{
                "updateImageProperties": {
                    "objectId": "{object_id}",
                    "imageProperties": "{image_properties}",
                    "fields": "{fields}"
                }
            }],
            'parameters': ['object_id', 'image_properties', 'fields'],
            'dependencies': ['object_id']
        },
        {
            'intent_type': 'replaceImage',
            'description': 'Replace existing image with new one',
            'api_template': [{
                "replaceImage": {
                    "imageObjectId": "{image_object_id}",
                    "url": "{url}",
                    "imageReplaceMethod": "{replace_method}"
                }
            }],
            'parameters': ['image_object_id', 'url', 'replace_method'],
            'dependencies': ['image_object_id']
        },
        {
            'intent_type': 'replaceAllShapesWithImage',
            'description': 'Replace shapes matching criteria with image',
            'api_template': [{
                "replaceAllShapesWithImage": {
                    "imageUrl": "{image_url}",
                    "imageReplaceMethod": "{replace_method}",
                    "containsText": {
                        "text": "{search_text}",
                        "matchCase": "{match_case}"
                    }
                }
            }],
            'parameters': ['image_url', 'replace_method', 'search_text', 'match_case'],
            'dependencies': []
        },
        
        # VIDEO OPERATIONS
        {
            'intent_type': 'createVideo',
            'description': 'Create video from source',
            'api_template': [{
                "createVideo": {
                    "objectId": "{object_id}",
                    "source": "{video_source}",
                    "id": "{video_id}",
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
            'parameters': ['object_id', 'slide_id', 'video_source', 'video_id', 'height', 'width', 'x_position', 'y_position'],
            'dependencies': ['slide_id']
        },
        {
            'intent_type': 'updateVideoProperties',
            'description': 'Update video properties',
            'api_template': [{
                "updateVideoProperties": {
                    "objectId": "{object_id}",
                    "videoProperties": "{video_properties}",
                    "fields": "{fields}"
                }
            }],
            'parameters': ['object_id', 'video_properties', 'fields'],
            'dependencies': ['object_id']
        },
        
        # CHART OPERATIONS
        {
            'intent_type': 'createSheetsChart',
            'description': 'Create Google Sheets chart',
            'api_template': [{
                "createSheetsChart": {
                    "objectId": "{object_id}",
                    "spreadsheetId": "{spreadsheet_id}",
                    "chartId": "{chart_id}",
                    "linkingMode": "{linking_mode}",
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
            'parameters': ['object_id', 'slide_id', 'spreadsheet_id', 'chart_id', 'linking_mode', 'height', 'width', 'x_position', 'y_position'],
            'dependencies': ['slide_id']
        },
        {
            'intent_type': 'refreshSheetsChart',
            'description': 'Refresh Google Sheets chart with latest data',
            'api_template': [{
                "refreshSheetsChart": {
                    "objectId": "{chart_object_id}"
                }
            }],
            'parameters': ['chart_object_id'],
            'dependencies': ['chart_object_id']
        },
        {
            'intent_type': 'replaceAllShapesWithSheetsChart',
            'description': 'Replace shapes matching criteria with Google Sheets chart',
            'api_template': [{
                "replaceAllShapesWithSheetsChart": {
                    "containsText": {
                        "text": "{search_text}",
                        "matchCase": "{match_case}"
                    },
                    "spreadsheetId": "{spreadsheet_id}",
                    "chartId": "{chart_id}",
                    "linkingMode": "{linking_mode}"
                }
            }],
            'parameters': ['search_text', 'match_case', 'spreadsheet_id', 'chart_id', 'linking_mode'],
            'dependencies': []
        },
        
        # LINE OPERATIONS
        {
            'intent_type': 'createLine',
            'description': 'Create line connector',
            'api_template': [{
                "createLine": {
                    "objectId": "{object_id}",
                    "category": "{line_category}",
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
            'parameters': ['object_id', 'slide_id', 'line_category', 'height', 'width', 'x_position', 'y_position'],
            'dependencies': ['slide_id']
        },
        {
            'intent_type': 'updateLineProperties',
            'description': 'Update line properties',
            'api_template': [{
                "updateLineProperties": {
                    "objectId": "{object_id}",
                    "lineProperties": "{line_properties}",
                    "fields": "{fields}"
                }
            }],
            'parameters': ['object_id', 'line_properties', 'fields'],
            'dependencies': ['object_id']
        },
        {
            'intent_type': 'updateLineCategory',
            'description': 'Update line category',
            'api_template': [{
                "updateLineCategory": {
                    "objectId": "{object_id}",
                    "lineCategory": "{line_category}"
                }
            }],
            'parameters': ['object_id', 'line_category'],
            'dependencies': ['object_id']
        },
        {
            'intent_type': 'rerouteLine',
            'description': 'Reroute line connector',
            'api_template': [{
                "rerouteLine": {
                    "objectId": "{object_id}"
                }
            }],
            'parameters': ['object_id'],
            'dependencies': ['object_id']
        },
        
        # GENERAL OBJECT OPERATIONS
        {
            'intent_type': 'deleteObject',
            'description': 'Delete page element or slide',
            'api_template': [{
                "deleteObject": {
                    "objectId": "{object_id}"
                }
            }],
            'parameters': ['object_id'],
            'dependencies': ['object_id']
        },
        {
            'intent_type': 'duplicateObject',
            'description': 'Duplicate slide or page element',
            'api_template': [{
                "duplicateObject": {
                    "objectId": "{object_id}",
                    "objectIds": "{object_id_mapping}"
                }
            }],
            'parameters': ['object_id', 'object_id_mapping'],
            'dependencies': ['object_id']
        },
        {
            'intent_type': 'updatePageElementTransform',
            'description': 'Update position, size, rotation of element',
            'api_template': [{
                "updatePageElementTransform": {
                    "objectId": "{object_id}",
                    "transform": "{transform}",
                    "applyMode": "{apply_mode}"
                }
            }],
            'parameters': ['object_id', 'transform', 'apply_mode'],
            'dependencies': ['object_id']
        },
        {
            'intent_type': 'updatePageElementsZOrder',
            'description': 'Update Z-order (layering) of page elements',
            'api_template': [{
                "updatePageElementsZOrder": {
                    "pageElementObjectIds": "{page_element_object_ids}",
                    "operation": "{z_order_operation}"
                }
            }],
            'parameters': ['page_element_object_ids', 'z_order_operation'],
            'dependencies': []
        },
        {
            'intent_type': 'updatePageElementAltText',
            'description': 'Update alt text for accessibility',
            'api_template': [{
                "updatePageElementAltText": {
                    "objectId": "{object_id}",
                    "title": "{alt_title}",
                    "description": "{alt_description}"
                }
            }],
            'parameters': ['object_id', 'alt_title', 'alt_description'],
            'dependencies': ['object_id']
        },
        
        # PAGE OPERATIONS
        {
            'intent_type': 'updatePageProperties',
            'description': 'Update slide background and properties',
            'api_template': [{
                "updatePageProperties": {
                    "objectId": "{slide_id}",
                    "pageProperties": "{page_properties}",
                    "fields": "{fields}"
                }
            }],
            'parameters': ['slide_id', 'page_properties', 'fields'],
            'dependencies': ['slide_id']
        },
        
        # GROUPING OPERATIONS
        {
            'intent_type': 'groupObjects',
            'description': 'Group multiple objects together',
            'api_template': [{
                "groupObjects": {
                    "groupObjectId": "{group_object_id}",
                    "childrenObjectIds": "{children_object_ids}"
                }
            }],
            'parameters': ['group_object_id', 'children_object_ids'],
            'dependencies': []
        },
        {
            'intent_type': 'ungroupObjects',
            'description': 'Ungroup objects',
            'api_template': [{
                "ungroupObjects": {
                    "objectIds": "{object_ids}"
                }
            }],
            'parameters': ['object_ids'],
            'dependencies': []
        }
    ]

    try:
        cur = conn.cursor()
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
        logging.info(f"Seeded {len(intents)} API intents - complete coverage")
        return True
    except Exception as e:
        logging.error(f"Error seeding intents: {e}")
        return False
    finally:
        cur.close()
        conn.close()

# Simple helper functions - no unnecessary complexity
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

def get_presentation_objects(presentation_id: str):
    """Get all objects for a presentation - returns object_id as requested"""
    conn = get_db_connection()
    if not conn:
        return []

    try:
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cur.execute("SELECT * FROM presentation_objects WHERE presentation_id = %s", (presentation_id,))
        return [dict(row) for row in cur.fetchall()]
    except Exception as e:
        logging.error(f"Error getting objects: {e}")
        return []
    finally:
        cur.close()
        conn.close()

# Existing functions moved here - no duplication
def save_presentation_to_db(presentation_id, presentation_title, instructions_text, email_address):
    """Save presentation data"""
    conn = get_db_connection()
    if not conn:
        return False

    try:
        cur = conn.cursor()
        cur.execute("""
            INSERT INTO presentations (presentation_id, presentation_title, instructions_text, email_address)
            VALUES (%s, %s, %s, %s)
            ON CONFLICT (presentation_id) DO UPDATE SET
                presentation_title = EXCLUDED.presentation_title,
                instructions_text = EXCLUDED.instructions_text,
                email_address = EXCLUDED.email_address
        """, (presentation_id, presentation_title, instructions_text, email_address))
        conn.commit()
        return True
    except Exception as e:
        logging.error(f"Error saving presentation: {e}")
        return False
    finally:
        cur.close()
        conn.close()

def record_error(presentation_id: str, error_code: str, error_msg: str):
    """Record API error"""
    conn = get_db_connection()
    if not conn:
        return False

    try:
        cur = conn.cursor()
        cur.execute(
            "INSERT INTO slide_errors (presentation_id, error_code, error_msg) VALUES (%s, %s, %s)",
            (presentation_id, error_code, error_msg)
        )
        conn.commit()
        return True
    except Exception as e:
        logging.error(f"Error recording error: {e}")
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
